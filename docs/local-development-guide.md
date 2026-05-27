# Local Development Guide

This guide explains how to run Scope locally with the backend API, frontend web app, Google sign-in, SQLite storage, and local artifacts.

## 1. Prerequisites

Install:

- Python 3.13+
- `uv`
- Node.js 20+
- npm
- Git

Optional but useful:

- Google OAuth client ID for real sign-in
- OpenAI/Gemini/search provider keys for real research runs

## 2. Environment Setup

From repo root:

```bash
cp .env.example .env
```

Minimum useful local `.env`:

```bash
OPENAI_API_KEY=
EXA_API_KEY=
GEMINI_API_KEY=

GOOGLE_CLIENT_ID=
VITE_GOOGLE_CLIENT_ID=
JWT_SECRET=replace-with-random-secret

SCOPE_DB_BACKEND=sqlite
SCOPE_DB_PATH=storage/research_runs.db
SCOPE_STORAGE_DIR=storage
SCOPE_ARTIFACTS_DIR=storage/artifacts
ARTIFACT_STORE_BACKEND=local

RESEARCH_MAX_WORKERS=2
RESEARCH_QUEUE_DEPTH=200
```

Generate a local JWT secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

If you want real Google sign-in, follow [Google OAuth Setup Guide](./google-oauth-setup.md).

## 3. Install Backend Dependencies

From repo root:

```bash
uv sync
```

If the lockfile is not being used:

```bash
uv pip install -e .
```

## 4. Install Frontend Dependencies

```bash
cd apps/web
npm install
```

## 5. Run Backend API

From repo root:

```bash
uv run python api_main.py
```

Default backend:

```text
http://localhost:8000
```

Useful endpoints:

```text
GET  /health
GET  /api/v1/health
POST /research-runs
GET  /research-runs
POST /api/v1/auth/google
POST /api/v1/advisor/runs
```

The backend initializes the local SQLite database automatically. You can also run migrations manually:

```bash
uv run python scripts/migrate_db.py
uv run python scripts/validate_migrations.py
```

## 6. Run Frontend

In a second terminal:

```bash
cd apps/web
npm run dev
```

Open:

```text
http://localhost:3000
```

The frontend reads:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_GOOGLE_CLIENT_ID=...
```

If `VITE_API_BASE_URL` is not set, it defaults to `http://localhost:8000`.

## 7. Local Runtime State

Local SQLite database:

```text
storage/research_runs.db
```

Local artifacts:

```text
storage/artifacts/
```

Generated frontend build:

```text
apps/web/dist/
```

These are runtime/generated files and should not be treated as source of truth.

## 8. Running The Core Product Flow

1. Start backend.
2. Start frontend.
3. Sign in with Google.
4. Open **Profile** and complete onboarding.
5. Open **New Research**.
6. Enter company name and ticker.
7. Wait for the research run to complete.
8. Review the final synthesis.
9. Open **Advisor** and ask follow-up questions.
10. Open **Archive** to reopen saved runs.

## 9. Verification Commands

Run backend tests:

```bash
uv run python -m pytest tests/test_*.py
```

Export OpenAPI:

```bash
uv run python scripts/export_openapi.py
```

Validate migrations:

```bash
uv run python scripts/validate_migrations.py
```

Run advisor evals:

```bash
uv run python evaluations/advisor/deepeval_runner.py
```

Build frontend:

```bash
cd apps/web
npm run build
```

## 10. Troubleshooting

### Frontend cannot reach backend

Check backend is running:

```bash
curl http://localhost:8000/health
```

Check frontend env:

```bash
VITE_API_BASE_URL=http://localhost:8000
```

Restart `npm run dev` after changing frontend env vars.

### Google sign-in fails

Check:

- `GOOGLE_CLIENT_ID` is set in backend env.
- `VITE_GOOGLE_CLIENT_ID` is set in frontend env.
- The OAuth client allows `http://localhost:3000`.

### Research is slow or appears stalled

Research uses external model/search/document providers. Slow stages are usually:

- Gemini grounding
- primary document discovery
- PDF download/parsing
- final synthesis

Check backend logs and run status fields:

- `current_stage`
- `current_substep`
- `last_activity_at`
- `is_stalled`

### Reset local state

Stop the backend, then move or delete:

```bash
storage/research_runs.db
storage/artifacts/
```

Do this only for local development.
