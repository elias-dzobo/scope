"""Pipeline orchestration primitives for research run execution.

This module introduces a production-oriented execution pattern while keeping the
implementation simple enough to run locally.

Current behavior:
- bounded queue for pending jobs
- fixed-size worker pool
- retry policy with exponential backoff
- structured lifecycle logs/callbacks for start/success/failure

Extension path:
- This adapter can later be replaced by a durable queue backend (Redis/Celery/
  Temporal) while preserving the same submit API.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from queue import Empty, PriorityQueue
from typing import Any, Callable

from scope_api.observability.metrics import record_orchestrator_queue_depth, record_orchestrator_retry
from research_core.utils.logger import get_logger

logger = get_logger(__name__)

FailureHandler = Callable[[str, BaseException, int], None]


@dataclass(frozen=True)
class OrchestrationJob:
    """Represents one run invocation in the orchestration queue."""

    run_id: str
    company_name: str
    ticker: str
    selected_pillars: list[str]
    attempt: int = 0
    enqueued_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class OrchestratorConfig:
    """Configuration for the local orchestrator.

    Keeping defaults low helps local/dev environments stay deterministic.
    """

    max_workers: int = 2
    max_queue_depth: int = 200
    max_retries: int = 1
    retry_base_seconds: float = 2.0
    worker_name_prefix: str = "scope-worker"


@dataclass
class OrchestrationStats:
    """Mutable metrics exposed for observability and tests."""

    submitted: int = 0
    completed: int = 0
    failed: int = 0
    retrying: int = 0
    dropped_full_queue: int = 0


class InProcessOrchestrator:
    """In-process worker pool for background research runs.

    Tradeoff (explicit):
    - ✅ no extra infrastructure
    - ❗not durable across process restarts

    This is intended as an intermediate implementation and adapter boundary for
    durable backends.
    """

    def __init__(
        self,
        runner: Callable[[str, str, str, list[str]], Any],
        config: OrchestratorConfig | None = None,
        on_job_failed: FailureHandler | None = None,
    ) -> None:
        self._runner = runner
        self._on_job_failed = on_job_failed
        self._config = config or OrchestratorConfig()
        self._queue: PriorityQueue[tuple[float, int, OrchestrationJob]] = PriorityQueue(
            maxsize=self._config.max_queue_depth
        )
        self._counter = 0
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._stats = OrchestrationStats()
        self._workers: list[threading.Thread] = []
        record_orchestrator_queue_depth(self._queue.qsize(), len(self._workers))

    def start(self) -> None:
        """Start worker threads once."""
        if self._workers:
            return

        for idx in range(self._config.max_workers):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"{self._config.worker_name_prefix}-{idx + 1}",
                daemon=True,
            )
            thread.start()
            self._workers.append(thread)
        record_orchestrator_queue_depth(self._queue.qsize(), len(self._workers))

    @property
    def workers(self) -> list[threading.Thread]:
        """Current worker handles. Exposed for health diagnostics."""
        return self._workers

    def get_stats(self) -> dict[str, int]:
        """Snapshot of key run-execution counters."""
        return {
            "submitted": self._stats.submitted,
            "completed": self._stats.completed,
            "failed": self._stats.failed,
            "retrying": self._stats.retrying,
            "dropped_full_queue": self._stats.dropped_full_queue,
            "queued": self._queue.qsize(),
        }

    def queue_depth(self) -> int:
        """Alias for the current queue depth metric."""
        return self._queue.qsize()

    def submit(self, job: OrchestrationJob) -> bool:
        """Submit job for async execution.

        Returns False when queue is saturated. Callers can surface 429/retry semantics.
        """
        scheduled_at = time.time()
        with self._lock:
            if self._queue.full():
                self._stats.dropped_full_queue += 1
                logger.warning(
                    "Failed to enqueue run_id=%s because queue is full",
                    job.run_id,
                )
                return False

            self._counter += 1
            self._queue.put((scheduled_at, self._counter, job))
            self._stats.submitted += 1
            logger.debug("Enqueued run_id=%s | attempt=%d", job.run_id, job.attempt)
            record_orchestrator_queue_depth(self._queue.qsize(), len(self._workers))
            return True

    def stop(self) -> None:
        """Stop workers gracefully after current jobs complete."""
        self._shutdown.set()
        for thread in self._workers:
            if thread.is_alive():
                thread.join(timeout=2.0)
        record_orchestrator_queue_depth(self._queue.qsize(), 0)

    def _worker_loop(self) -> None:
        """Consume jobs and execute with retry policy."""
        while not self._shutdown.is_set():
            try:
                scheduled_at, seq, job = self._queue.get(timeout=0.5)
            except Empty:
                continue

            now = time.time()
            if scheduled_at > now:
                delay = scheduled_at - now
                try:
                    self._queue.put((scheduled_at, seq, job), timeout=0.2)
                except Exception:
                    logger.debug("Unable to requeue delayed job id=%s", job.run_id)
                time.sleep(delay)
                continue

            try:
                self._runner(job.run_id, job.company_name, job.ticker, job.selected_pillars)
            except Exception as exc:
                if job.attempt < self._config.max_retries:
                    self._stats.retrying += 1
                    next_attempt = job.attempt + 1
                    retry_after = self._retry_delay(next_attempt)
                    record_orchestrator_retry("retryable_failure")
                    next_job = OrchestrationJob(
                        run_id=job.run_id,
                        company_name=job.company_name,
                        ticker=job.ticker,
                        selected_pillars=job.selected_pillars,
                        attempt=next_attempt,
                    )
                    next_schedule = time.time() + retry_after
                    self._queue.put((next_schedule, seq, next_job))
                    logger.warning(
                        "Run_id=%s failed on attempt=%d; retrying in %.2fs: %s",
                        job.run_id,
                        job.attempt,
                        retry_after,
                        exc,
                    )
                else:
                    self._stats.failed += 1
                    record_orchestrator_retry("permanent_failure")
                    if self._on_job_failed is not None:
                        self._on_job_failed(job.run_id, exc, job.attempt)
                    logger.exception(
                        "Run_id=%s failed permanently after attempt=%d",
                        job.run_id,
                        job.attempt,
                    )
            else:
                self._stats.completed += 1
            finally:
                record_orchestrator_queue_depth(self._queue.qsize(), len(self._workers))
                self._queue.task_done()

    def _retry_delay(self, attempt: int) -> float:
        """Compute exponential backoff in seconds."""
        return self._config.retry_base_seconds * (2 ** (attempt - 1))


class OrchestrationManager:
    """Facade exposing a single object with lifecycle methods used by service layer."""

    def __init__(
        self,
        runner: Callable[[str, str, str, list[str]], None],
        on_job_failed: FailureHandler | None = None,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self._orchestrator = InProcessOrchestrator(
            runner=runner,
            config=config,
            on_job_failed=on_job_failed,
        )

    def start(self) -> None:
        """Start execution workers."""
        self._orchestrator.start()

    def submit(self, run_id: str, company_name: str, ticker: str, selected_pillars: list[str]) -> bool:
        """Submit a run to async execution.

        Returns False if queue admission is blocked.
        """
        job = OrchestrationJob(
            run_id=run_id,
            company_name=company_name,
            ticker=ticker,
            selected_pillars=selected_pillars,
        )
        return self._orchestrator.submit(job)

    def shutdown(self) -> None:
        """Stop worker threads."""
        self._orchestrator.stop()

    def get_health(self) -> dict[str, int]:
        """Expose health/observability fields consumed by monitoring endpoints."""
        return {
            **self._orchestrator.get_stats(),
            "workers": len(self._orchestrator.workers),
        }
