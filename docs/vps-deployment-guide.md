# VPS Deployment Guide

This guide describes a practical production deployment for Scope on a VPS. It covers backend, frontend, Postgres, MinIO, domain setup, TLS, process management, scaling, and security.

The recommended first production topology:

```text
Internet
  |
  v
Nginx / Caddy reverse proxy
  |---------------------------|
  v                           v
Frontend static app           FastAPI backend
apps/web/dist                 uvicorn/gunicorn
                              |
                              |---- Postgres
                              |---- MinIO
                              |---- provider APIs
```

## 1. VPS Requirements

For an early production/beta deployment:

- Ubuntu 22.04 or 24.04 LTS
- 2-4 vCPU
- 4-8 GB RAM minimum
- 80+ GB disk
- SSH key access
- Firewall enabled

For heavier research workloads:

- 4-8 vCPU
- 16 GB RAM
- separate volume for Postgres/MinIO data
- swap enabled

Research runs can be slow and memory-heavy because they use external search, documents, PDFs, and LLM calls. Start conservative and scale workers gradually.

## 2. DNS And Domain Setup

Buy a domain from any registrar:

- Namecheap
- Cloudflare Registrar
- Porkbun
- Google Domains/Squarespace
- GoDaddy

Recommended DNS:

```text
scope.your-domain.com      A      <VPS_PUBLIC_IP>
api.scope.your-domain.com  A      <VPS_PUBLIC_IP>
```

You can also use one domain:

```text
your-domain.com      frontend
your-domain.com/api  backend proxy
```

For clarity, separate subdomains are better:

- `scope.your-domain.com` for frontend
- `api.scope.your-domain.com` for backend

After DNS changes:

```bash
dig scope.your-domain.com
dig api.scope.your-domain.com
```

## 3. Server Bootstrap

SSH into the VPS:

```bash
ssh ubuntu@<VPS_PUBLIC_IP>
```

Install packages:

```bash
sudo apt update
sudo apt install -y git curl build-essential nginx ufw python3.13 python3.13-venv nodejs npm postgresql postgresql-contrib
```

Install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart shell or source the uv path:

```bash
source ~/.bashrc
```

Create app user:

```bash
sudo adduser --disabled-password --gecos "" scope
sudo usermod -aG sudo scope
```

Deploy code under:

```text
/opt/scope
```

```bash
sudo mkdir -p /opt/scope
sudo chown scope:scope /opt/scope
```

## 4. Clone And Install App

```bash
sudo -iu scope
cd /opt/scope
git clone <YOUR_REPO_URL> .
uv sync
cd apps/web
npm ci
npm run build
```

## 5. Postgres Setup

Create database and user:

```bash
sudo -u postgres psql
```

Inside psql:

```sql
CREATE DATABASE scope;
CREATE USER scope_user WITH PASSWORD 'replace-with-strong-password';
GRANT ALL PRIVILEGES ON DATABASE scope TO scope_user;
\q
```

Use a strong password. Store it only in server environment files.

Production DB env:

```bash
SCOPE_DB_BACKEND=postgres
DATABASE_URL=postgresql://scope_user:replace-with-strong-password@localhost:5432/scope
```

Run migrations:

```bash
cd /opt/scope
uv run python scripts/migrate_db.py
uv run python scripts/validate_migrations.py
```

## 6. MinIO Setup

MinIO gives us S3-compatible object storage on the VPS.

Install using Docker Compose or native systemd. Docker is usually simpler:

```bash
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

Create MinIO directories:

```bash
sudo mkdir -p /opt/minio/data
sudo chown -R 1000:1000 /opt/minio
```

Create `/opt/minio/docker-compose.yml`:

```yaml
services:
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: scope_minio
      MINIO_ROOT_PASSWORD: replace-with-long-random-password
    ports:
      - "127.0.0.1:9000:9000"
      - "127.0.0.1:9001:9001"
    volumes:
      - /opt/minio/data:/data
    restart: unless-stopped
```

Start:

```bash
cd /opt/minio
sudo docker compose up -d
```

Create bucket:

```bash
sudo docker exec -it minio-minio-1 sh
```

Inside container:

```bash
mc alias set local http://localhost:9000 scope_minio replace-with-long-random-password
mc mb local/scope-artifacts
exit
```

Scope artifact env:

```bash
ARTIFACT_STORE_BACKEND=minio
ARTIFACT_BUCKET=scope-artifacts
ARTIFACT_PREFIX=scope
ARTIFACT_S3_ENDPOINT_URL=http://127.0.0.1:9000
ARTIFACT_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=scope_minio
AWS_SECRET_ACCESS_KEY=replace-with-long-random-password
```

Keep MinIO private to the server at first. Public signed URLs can come later.

## 7. Production Environment File

Create:

```bash
sudo mkdir -p /etc/scope
sudo nano /etc/scope/scope.env
```

Example:

```bash
OPENAI_API_KEY=...
EXA_API_KEY=...
GEMINI_API_KEY=...

GOOGLE_CLIENT_ID=your-production-google-client-id.apps.googleusercontent.com
JWT_SECRET=replace-with-long-random-secret
JWT_ISSUER=scope
JWT_AUDIENCE=scope-web
JWT_EXPIRES_MINUTES=1440
AUTH_ALLOW_DEV_GOOGLE_TOKEN=false

SCOPE_DB_BACKEND=postgres
DATABASE_URL=postgresql://scope_user:password@localhost:5432/scope

SCOPE_STORAGE_DIR=/var/lib/scope
SCOPE_ARTIFACTS_DIR=/var/lib/scope/artifacts
ARTIFACT_STORE_BACKEND=minio
ARTIFACT_BUCKET=scope-artifacts
ARTIFACT_PREFIX=scope
ARTIFACT_S3_ENDPOINT_URL=http://127.0.0.1:9000
ARTIFACT_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=scope_minio
AWS_SECRET_ACCESS_KEY=replace-with-long-random-password

RESEARCH_MAX_WORKERS=2
RESEARCH_QUEUE_DEPTH=200
RESEARCH_MAX_RETRIES=1
RESEARCH_RETRY_BASE_SECONDS=2

OBSERVABILITY_ENABLED=false
```

Secure it:

```bash
sudo chown root:scope /etc/scope/scope.env
sudo chmod 640 /etc/scope/scope.env
```

## 8. Backend systemd Service

Create:

```bash
sudo nano /etc/systemd/system/scope-api.service
```

Service:

```ini
[Unit]
Description=Scope FastAPI Backend
After=network.target postgresql.service docker.service

[Service]
User=scope
Group=scope
WorkingDirectory=/opt/scope
EnvironmentFile=/etc/scope/scope.env
ExecStart=/home/scope/.local/bin/uv run uvicorn scope_api.app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now scope-api
sudo systemctl status scope-api
```

Logs:

```bash
journalctl -u scope-api -f
```

## 9. Nginx Reverse Proxy

Frontend static files:

```text
/opt/scope/apps/web/dist
```

Create:

```bash
sudo nano /etc/nginx/sites-available/scope
```

Config:

```nginx
server {
    listen 80;
    server_name scope.your-domain.com;

    root /opt/scope/apps/web/dist;
    index index.html;

    location / {
        try_files $uri /index.html;
    }
}

server {
    listen 80;
    server_name api.scope.your-domain.com;

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 60;
        proxy_send_timeout 300;
    }
}
```

Enable:

```bash
sudo ln -s /etc/nginx/sites-available/scope /etc/nginx/sites-enabled/scope
sudo nginx -t
sudo systemctl reload nginx
```

## 10. TLS With Let’s Encrypt

Install Certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
```

Issue certificates:

```bash
sudo certbot --nginx -d scope.your-domain.com -d api.scope.your-domain.com
```

Test renewal:

```bash
sudo certbot renew --dry-run
```

Update Google OAuth authorized origins:

```text
https://scope.your-domain.com
```

Production frontend env at build time:

```bash
VITE_API_BASE_URL=https://api.scope.your-domain.com
VITE_GOOGLE_CLIENT_ID=your-production-google-client-id.apps.googleusercontent.com
```

Rebuild frontend after changing `VITE_*`:

```bash
cd /opt/scope/apps/web
npm run build
```

## 11. Firewall

Allow only SSH, HTTP, HTTPS:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Do not expose:

- Postgres `5432`
- MinIO `9000`
- MinIO console `9001`
- backend `8000`

All should stay bound to localhost or private networks.

## 12. Deployment Workflow

Basic deploy:

```bash
sudo -iu scope
cd /opt/scope
git pull
uv sync
uv run python scripts/migrate_db.py
uv run python scripts/validate_migrations.py
cd apps/web
npm ci
VITE_API_BASE_URL=https://api.scope.your-domain.com VITE_GOOGLE_CLIENT_ID=... npm run build
sudo systemctl restart scope-api
sudo systemctl reload nginx
```

Verify:

```bash
curl https://api.scope.your-domain.com/health
curl https://api.scope.your-domain.com/api/v1/health
```

Open:

```text
https://scope.your-domain.com
```

## 13. Scaling Strategy

Scope has two scaling surfaces:

1. API traffic
2. research execution workload

### Vertical scaling first

For early users:

- increase VPS CPU/RAM
- increase `RESEARCH_MAX_WORKERS` carefully
- use Postgres and MinIO on the same VPS

Suggested starting point:

```bash
RESEARCH_MAX_WORKERS=2
RESEARCH_QUEUE_DEPTH=200
```

Raise to 4 only if:

- CPU and memory have headroom
- external provider rate limits are not being hit
- Postgres remains healthy

### Separate services later

When usage grows:

- move Postgres to managed Postgres or separate VPS
- move MinIO to separate volume/server
- split research workers from API web process
- introduce Redis/queue worker if in-process queue becomes limiting
- add horizontal backend replicas behind Nginx/load balancer

### Provider rate limits

Research bottlenecks often come from:

- OpenAI/Gemini rate limits
- search-provider quotas
- PDF/doc fetch latency
- browser rendering fallback

Track:

- average research duration
- queue depth
- failed provider calls
- retry counts
- cost per research run

## 14. Security Checklist

Application:

- `AUTH_ALLOW_DEV_GOOGLE_TOKEN=false`
- strong `JWT_SECRET`
- Google OAuth origins restricted to production frontend domain
- no secrets committed to Git
- `RESEARCH_API_KEY` enabled if exposing protected admin/API paths
- CORS restricted if/when CORS config is added

Server:

- SSH key login only
- disable password SSH login
- UFW enabled
- unattended security updates enabled
- Nginx only exposes 80/443
- Postgres and MinIO not public
- regular backups for Postgres and MinIO

Database:

- least-privilege database user
- strong password
- migration validation before deploy
- regular `pg_dump` backups

Artifacts:

- MinIO bucket private
- do not expose raw PDFs or user artifacts publicly
- signed URLs only when the product needs direct downloads

Observability:

- enable logs and metrics before wider beta
- alert on backend restarts, disk usage, failed jobs, and queue depth

## 15. Backups

Postgres:

```bash
pg_dump "$DATABASE_URL" > scope-$(date +%F).sql
```

MinIO:

- snapshot `/opt/minio/data`
- or mirror bucket to external S3-compatible storage

App env:

- back up `/etc/scope/scope.env` securely
- never store it in the repo

## 16. Production Readiness Gates

Before inviting real users:

```bash
uv run python -m pytest tests/test_*.py
uv run python scripts/export_openapi.py
uv run python scripts/validate_migrations.py
uv run python evaluations/advisor/deepeval_runner.py
cd apps/web && npm run build
```

Then manually test:

1. Google sign-in.
2. Onboarding profile save/update.
3. New company research.
4. Completed final synthesis display.
5. Saved research archive.
6. Advisor follow-up question.
7. Artifact manifest entries.
8. Backend logs for errors.
