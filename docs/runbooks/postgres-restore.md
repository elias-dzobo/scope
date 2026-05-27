# Postgres Restore Runbook

1. Stop API:
   `sudo systemctl stop scope-api`
2. Confirm backup path:
   `/opt/scope/backups/scope-YYYYMMDD-HHMMSS.sql.gz`
3. Restore:
   `SCOPE_ENV_FILE=/etc/scope/scope.env scripts/restore_postgres.sh <backup.sql.gz>`
4. Validate schema:
   `uv run python scripts/validate_migrations.py`
5. Start API:
   `sudo systemctl start scope-api`
6. Smoke test login, research list, advisor conversation, and artifacts.
