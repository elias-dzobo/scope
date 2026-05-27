# Scope API

Production entrypoint for the FastAPI backend.

Local:

```bash
uv run python api_main.py
```

Production service:

```bash
uv run uvicorn scope_api.app:app --host 127.0.0.1 --port 8000
```

Required production checks:

```bash
uv run python scripts/check_production_config.py /etc/scope/scope.env
uv run python scripts/validate_migrations.py
uv run python scripts/export_openapi.py
```

Private beta keeps research workers in-process. The `scope-worker` systemd
template is a placeholder until DB-backed leasing is implemented.
