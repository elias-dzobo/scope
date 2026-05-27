# Research Queue Stuck Runbook

1. Check `/health` for orchestrator stats.
2. Check logs:
   `journalctl -u scope-api -f`
3. Verify provider keys and network access.
4. Restart API for private beta in-process workers:
   `sudo systemctl restart scope-api`
5. Inspect failed runs in Postgres.
6. Before paid beta, use the durable worker lease recovery flow once implemented.
