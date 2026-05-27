from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scope_api import db
from scope_api.application.run_service import ResearchRunService


def test_durable_backend_queues_without_in_process_submission(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setenv("RESEARCH_EXECUTION_BACKEND", "durable")
    db.init_db()

    service = ResearchRunService()
    run_id = service.submit_run("Nvidia", "NVDA", ["Macro & Industry"])

    run = db.get_run(run_id)
    assert run is not None
    assert run["status"] == "queued"
    assert run["current_stage"] == "queued"
    assert run["budget_snapshot"]["pricingModel"] == "hybrid_subscription_credits"


def test_research_run_lease_and_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    db.create_run("run-1", "Nvidia", "NVDA", ["Macro & Industry"], max_retries=2)

    leased = db.lease_next_research_run("worker-a", lease_seconds=120)

    assert leased is not None
    assert leased["id"] == "run-1"
    assert leased["status"] == "running"
    assert leased["lease_owner"] == "worker-a"
    assert leased["started_at"]
    assert db.renew_research_run_lease("run-1", "worker-a", lease_seconds=120)
    assert not db.renew_research_run_lease("run-1", "worker-b", lease_seconds=120)


def test_stale_research_run_can_be_released_for_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    db.create_run("run-1", "Nvidia", "NVDA", ["Macro & Industry"], max_retries=1)
    first = db.lease_next_research_run("worker-a", lease_seconds=120)
    assert first is not None

    db.release_research_run_for_retry("run-1", "provider timeout")
    retry = db.get_run("run-1")

    assert retry is not None
    assert retry["status"] == "queued"
    assert retry["current_stage"] == "retry_queued"
    assert retry["lease_owner"] == ""


def test_expired_running_run_is_released_to_another_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    db.create_run("run-1", "Nvidia", "NVDA", ["Macro & Industry"], max_retries=2)
    leased = db.lease_next_research_run("worker-a", lease_seconds=120)
    assert leased is not None

    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    conn = db._connect()  # noqa: SLF001 - test needs to simulate a dead worker lease.
    try:
        conn.execute("UPDATE research_runs SET lease_expires_at = ? WHERE id = ?", (expired_at, "run-1"))
        conn.commit()
    finally:
        conn.close()

    recovered = db.lease_next_research_run("worker-b", lease_seconds=120)

    assert recovered is not None
    assert recovered["id"] == "run-1"
    assert recovered["lease_owner"] == "worker-b"
    assert recovered["retry_count"] == 1


def test_completed_run_deletes_ephemeral_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "local")
    monkeypatch.setenv("SCOPE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ARTIFACT_RETENTION_MODE", "ephemeral")
    db.init_db()
    db.create_run("run-1", "Nvidia", "NVDA", ["Macro & Industry"], max_retries=1)

    raw_path = tmp_path / "artifacts" / "NVDA" / "documents" / "raw" / "doc.pdf"
    evidence_path = tmp_path / "artifacts" / "NVDA" / "evidence" / "document_facts.json"
    raw_path.parent.mkdir(parents=True)
    evidence_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"pdf")
    evidence_path.write_text("{}")

    service = ResearchRunService()
    kept = service._apply_artifact_retention(  # noqa: SLF001 - focused policy test.
        run_id="run-1",
        artifacts=[
            {
                "artifact_type": "raw_document",
                "storage_uri": str(raw_path),
                "content_type": "application/pdf",
                "size_bytes": 3,
                "checksum": "x",
                "metadata": {},
            },
            {
                "artifact_type": "document_evidence",
                "storage_uri": str(evidence_path),
                "content_type": "application/json",
                "size_bytes": 2,
                "checksum": "y",
                "metadata": {},
            },
        ],
    )

    assert [item["artifact_type"] for item in kept] == ["document_evidence"]
    assert not raw_path.exists()
    assert evidence_path.exists()
