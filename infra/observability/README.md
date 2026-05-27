# Scope Observability Stack

This folder contains a complete local stack for tracing + metrics + dashboards:

- `otel-collector` → receives OTLP traces from API/pipeline code
- `tempo` → stores traces for Grafana Tempo
- `prometheus` → scrapes `/metrics` from the FastAPI app
- `grafana` → dashboards for API, LLM, search, and orchestration metrics

## Run

```bash
cd /Users/eliasdzobo/Desktop/2026/scope
OBSERVABILITY_ENABLED=true OBSERVABILITY_METRICS_ENABLED=true uvicorn scope_api.app:app --host 0.0.0.0 --port 8000

cd infra/observability
docker compose up -d
```

## Required app environment variables

- `OBSERVABILITY_ENABLED=true`
- `OBSERVABILITY_METRICS_ENABLED=true`
- `OTEL_TRACES_ENABLED=true`
- `OTEL_SERVICE_NAME=scope-api`
- `OTEL_SERVICE_VERSION=0.1.0`
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`
- `OTEL_EXPORTER_OTLP_INSECURE=true`
- `OTEL_SAMPLING_RATIO=1.0`

## URLs

- API: `http://localhost:8000`
- Metrics: `http://localhost:8000/metrics`
- Prometheus: `http://localhost:9090`
- Tempo API: `http://localhost:3200`
- Grafana: `http://localhost:3000` (admin/admin)
- Dashboard: `Scope Stock Research - Platform Observability`
