"""Smoke-test the configured ArtifactStore and manifest persistence."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scope_api import db
from research_core.storage import artifact_store, ticker_artifact_key


def main() -> None:
    """Write a tiny artifact and register it in artifact_manifest."""
    db.init_db()
    store = artifact_store()
    payload = {"status": "ok", "kind": "artifact-store-smoke"}
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    checksum = hashlib.sha256(data).hexdigest()
    key = ticker_artifact_key("SMOKE", "ops", "artifact-store-smoke.json")
    uri = store.write_bytes(key, data, content_type="application/json")
    record = db.create_artifact_record(
        run_id="artifact-store-smoke",
        user_id="ops",
        ticker="SMOKE",
        artifact_type="smoke_test",
        storage_backend=store.backend_name,
        storage_uri=uri,
        content_type="application/json",
        size_bytes=len(data),
        checksum=checksum,
        metadata={"script": "scripts/smoke_artifact_store.py"},
    )
    print(json.dumps({"ok": True, "uri": uri, "artifactRecordId": record["id"]}, indent=2))


if __name__ == "__main__":
    main()
