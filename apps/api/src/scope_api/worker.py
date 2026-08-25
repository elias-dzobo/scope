"""Durable research worker entrypoint.

The worker leases queued research runs from the database and executes them one at
a time under a heartbeat-renewed lease. On Postgres it uses LISTEN/NOTIFY to wake
immediately when a run is queued, eliminating idle DB polling entirely. On SQLite
(local dev) it falls back to a polling loop.

Launched by Fly.io as a separate process group:
    worker = "python -m scope_api.worker"
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any

from scope_api import db
from scope_api.application.run_service import ResearchRunService
from research_core.utils.logger import configure_logging, get_logger

logger = get_logger(__name__)


class DurableResearchWorker:
    """Execute research runs leased from the database."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        poll_interval_seconds: float = 2.0,
        lease_seconds: int = 300,
        heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        self.worker_id = worker_id or _default_worker_id()
        self.poll_interval_seconds = max(poll_interval_seconds, 0.5)
        self.lease_seconds = max(lease_seconds, 30)
        self.heartbeat_interval_seconds = max(heartbeat_interval_seconds, 5.0)
        self._service = ResearchRunService()
        self._stop = threading.Event()

    def run_forever(self) -> None:
        """Run the worker loop until the process is terminated."""
        db.init_db()
        logger.info("Starting durable research worker | worker_id=%s", self.worker_id)
        if db.database_backend() == "postgres":
            self._run_forever_listen_notify()
        else:
            self._run_forever_polling()

    def stop(self) -> None:
        """Ask the worker loop to stop after the current run."""
        self._stop.set()

    # ── Postgres: event-driven via LISTEN/NOTIFY ──────────────────────────────

    def _run_forever_listen_notify(self) -> None:
        """Block on Postgres LISTEN instead of polling, waking instantly on each NOTIFY."""
        import psycopg

        url = os.environ["DATABASE_URL"]

        while not self._stop.is_set():
            try:
                with psycopg.connect(url, autocommit=True) as listen_conn:
                    listen_conn.execute("LISTEN new_research_run")
                    logger.info("LISTEN active | worker_id=%s", self.worker_id)

                    # Pick up any runs queued before this worker started listening.
                    self._drain_queued_runs()

                    while not self._stop.is_set():
                        # Blocks here at OS level — zero CPU, zero DB queries while idle.
                        # Wakes up the instant NOTIFY new_research_run fires.
                        # timeout=60 is a safety net: drain again in case a NOTIFY was
                        # somehow missed (e.g. a connection blip during the listen setup).
                        for _notify in listen_conn.notifies(timeout=60.0):
                            if self._stop.is_set():
                                return
                            self._drain_queued_runs()
                        self._drain_queued_runs()

            except Exception as exc:
                if self._stop.is_set():
                    return
                logger.warning("LISTEN connection lost, reconnecting in 5s | error=%s", exc)
                time.sleep(5)

    def _drain_queued_runs(self) -> None:
        """Lease and execute all currently queued runs before returning."""
        while not self._stop.is_set():
            run = db.lease_next_research_run(self.worker_id, lease_seconds=self.lease_seconds)
            if not run:
                return
            self._execute_leased_run(run)

    # ── SQLite: simple polling (local dev only) ───────────────────────────────

    def _run_forever_polling(self) -> None:
        while not self._stop.is_set():
            run = db.lease_next_research_run(self.worker_id, lease_seconds=self.lease_seconds)
            if not run:
                time.sleep(self.poll_interval_seconds)
                continue
            self._execute_leased_run(run)

    # ── Shared execution path ─────────────────────────────────────────────────

    def _execute_leased_run(self, run: dict[str, Any]) -> None:
        """Execute one leased research run and handle retry/failure state."""
        run_id = run["id"]
        db.append_event(
            run_id,
            "worker_lease",
            "running",
            {"workerId": self.worker_id, "leaseExpiresAt": run.get("lease_expires_at", "")},
        )
        heartbeat = _HeartbeatThread(
            run_id=run_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            interval_seconds=self.heartbeat_interval_seconds,
        )
        heartbeat.start()
        try:
            self._service.execute_run(
                run_id,
                run["company_name"],
                run["ticker"],
                list(run.get("selected_pillars") or []),
            )
        except Exception as exc:
            logger.exception("Durable research run failed | run_id=%s", run_id)
            db.append_event(run_id, "worker_error", "failed", {"workerId": self.worker_id, "error": str(exc)})
            db.release_research_run_for_retry(run_id, str(exc))
        finally:
            heartbeat.stop()
            heartbeat.join(timeout=2)


class _HeartbeatThread(threading.Thread):
    """Renew the run lease while a durable worker is executing the job."""

    def __init__(self, *, run_id: str, worker_id: str, lease_seconds: int, interval_seconds: float) -> None:
        super().__init__(name=f"scope-heartbeat-{run_id[:8]}", daemon=True)
        self._run_id = run_id
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            renewed = db.renew_research_run_lease(
                self._run_id,
                self._worker_id,
                lease_seconds=self._lease_seconds,
            )
            if not renewed:
                logger.warning("Unable to renew research lease | run_id=%s", self._run_id)
                return

    def stop(self) -> None:
        self._stop_event.set()


def _default_worker_id() -> str:
    suffix = os.getenv("FLY_MACHINE_ID") or os.getenv("HOSTNAME") or socket.gethostname()
    return f"scope-worker-{suffix}"


def main() -> None:
    """CLI entrypoint used by Fly process groups."""
    configure_logging()
    worker = DurableResearchWorker(
        poll_interval_seconds=float(os.getenv("RESEARCH_WORKER_POLL_SECONDS", "2")),
        lease_seconds=int(os.getenv("RESEARCH_LEASE_SECONDS", "300")),
        heartbeat_interval_seconds=float(os.getenv("RESEARCH_HEARTBEAT_SECONDS", "30")),
    )
    worker.run_forever()


if __name__ == "__main__":
    main()
