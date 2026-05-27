# Production Smoke Checklist

Run after every staging or production deploy.

- `curl -fsS https://api.scope.example.com/health`
- `curl -fsS https://api.scope.example.com/api/v1/health`
- Frontend loads over HTTPS.
- Google sign-in creates a session.
- Onboarding profile saves and reloads.
- Research run can be started.
- Research status can be polled.
- Completed final synthesis renders.
- Saved research opens without blank screen.
- Advisor conversation can be created and resumed.
- `uv run python scripts/smoke_artifact_store.py` succeeds.
- API logs show no startup errors.
