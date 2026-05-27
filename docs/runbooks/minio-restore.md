# MinIO Restore Runbook

1. Stop MinIO container:
   `cd /opt/minio && sudo docker compose down`
2. Restore `/opt/minio/data` from snapshot or mirror.
3. Start MinIO:
   `cd /opt/minio && sudo docker compose up -d`
4. Verify bucket exists and is private.
5. Run artifact smoke:
   `uv run python scripts/smoke_artifact_store.py`
