# Provider Outage Runbook

1. Check provider dashboards for OpenAI/Gemini/search status.
2. Inspect recent API logs for provider-specific errors.
3. Reduce `RESEARCH_MAX_WORKERS` if rate limits are being hit.
4. Switch `SEARCH_PROVIDER` only if the alternate provider key is configured.
5. Communicate degraded research freshness to beta users.
6. Re-run failed research jobs after provider recovery.
