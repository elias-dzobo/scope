# Failed Deploy Rollback Runbook

1. Check API logs:
   `journalctl -u scope-api -n 200 --no-pager`
2. Roll back code:
   `cd /opt/scope/app && git checkout <previous-good-tag>`
3. Reinstall locked dependencies:
   `uv sync --frozen`
4. Rebuild web:
   `cd apps/web && npm ci && npm run build`
5. Restart:
   `sudo systemctl restart scope-api && sudo systemctl reload nginx`
6. Verify:
   `curl -fsS https://api.scope.example.com/health`

Prefer forward-fix migrations. Restore the database only when the migration
changed data destructively and a verified backup exists.
