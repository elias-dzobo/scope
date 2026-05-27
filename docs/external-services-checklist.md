# External Services Checklist

Everything here requires an account, dashboard action, or credential that
cannot be done in the codebase alone. Work through these before running
`fly deploy`.

---

## 1. API Key Rotation — URGENT

All keys that were in `.env` are compromised and must be rotated before any
deployment. Do this first, before setting Fly secrets.

| Key | Dashboard | Status |
|-----|-----------|--------|
| `OPENAI_API_KEY` | platform.openai.com → API keys | ❌ Rotate now |
| `GEMINI_API_KEY` | aistudio.google.com or GCP console | ❌ Rotate now |
| `EXA_API_KEY` | exa.ai → dashboard | ❌ Rotate now |
| `TAVI_API_KEY` (Tavily) | tavily.com → dashboard | ❌ Rotate now |

Generate new keys scoped to production only. Keep a separate set for dev/staging.

---

## 2. Neon Postgres ✅ (connection string in hand)

- [ ] Confirm the connection string uses `sslmode=require`.
- [ ] Confirm you are using the **pooled** connection string for API runtime traffic (not the direct connection).
- [ ] If the direct string was ever pasted into chat or committed, rotate the Neon password now.
- [ ] Create a least-privilege app database role (not the project owner role) before public launch.
- [ ] Verify point-in-time restore is enabled on your Neon plan.

---

## 3. Fly.io

- [ ] Install the Fly CLI: `brew install flyctl` (or see fly.io/docs/hands-on/install-flyctl).
- [ ] Authenticate: `fly auth login`.
- [ ] Create the app: `fly apps create scope-api` (or your chosen app name — update `fly.toml` to match).
- [ ] Set all secrets in one pass after rotating keys above:
  ```bash
  fly secrets set \
    DATABASE_URL='postgresql://USER:PASSWORD@HOST/neondb?sslmode=require' \
    JWT_SECRET='<generate 32+ random chars>' \
    GOOGLE_CLIENT_ID='<production OAuth client id>' \
    GOOGLE_CLIENT_SECRET='<production OAuth client secret>' \
    OPENAI_API_KEY='<new key>' \
    GEMINI_API_KEY='<new key>' \
    EXA_API_KEY='<new key>' \
    TAVI_API_KEY='<new key>' \
    SCOPE_ALLOWED_ORIGINS='https://yourdomain.com' \
    ARTIFACT_BUCKET='scope-artifacts' \
    ARTIFACT_S3_ENDPOINT_URL='<tigris or r2 endpoint>' \
    AWS_ACCESS_KEY_ID='<key>' \
    AWS_SECRET_ACCESS_KEY='<secret>'
  ```
- [ ] Confirm secrets are set: `fly secrets list`.

---

## 4. Object Storage — Tigris or Cloudflare R2

Choose one. Tigris is native to Fly (simpler setup). R2 works fine too.

**Option A — Tigris (recommended, Fly native)**
- [ ] Enable Tigris for your Fly org: `fly storage create`.
- [ ] Note the endpoint URL, bucket name, access key, and secret from the output.
- [ ] Set `ARTIFACT_S3_ENDPOINT_URL` to the Tigris endpoint.

**Option B — Cloudflare R2**
- [ ] Create an R2 bucket in the Cloudflare dashboard.
- [ ] Generate an R2 API token with read/write access to the bucket.
- [ ] Set `ARTIFACT_S3_ENDPOINT_URL` to `https://<account_id>.r2.cloudflarestorage.com`.

Either way:
- [ ] Confirm the bucket is **not** publicly accessible.
- [ ] Run the artifact store smoke test after first deploy:
  ```bash
  fly ssh console -C "uv run python -m pytest tests/test_artifact_store.py -q"
  ```

---

## 5. Google OAuth — Production Client

The current `GOOGLE_CLIENT_ID` in `.env` was a dev client and is now compromised.
You need a production OAuth 2.0 client tied to your production domain.

- [ ] Go to console.cloud.google.com → APIs & Services → Credentials.
- [ ] Create a new OAuth 2.0 Client ID (Web application).
- [ ] Add authorized JavaScript origin: `https://yourdomain.com`.
- [ ] Add authorized redirect URI: `https://yourdomain.com/auth/callback`.
- [ ] Copy the new Client ID and Client Secret.
- [ ] Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` via `fly secrets set`.
- [ ] Set `VITE_GOOGLE_CLIENT_ID` to the same client ID for the frontend build.

---

## 6. Domain & DNS

- [ ] Register your domain (or confirm you already own it).
- [ ] In your DNS provider, point the domain at Fly:
  - Add a `CNAME` record: `app.yourdomain.com → scope-api.fly.dev`
  - Or use Fly's IP-based `A` records if you need an apex domain.
- [ ] Run `fly certs add yourdomain.com` to provision a TLS certificate.
- [ ] Confirm HTTPS is working: `curl -I https://yourdomain.com/health`.
- [ ] Update `SCOPE_ALLOWED_ORIGINS` secret to match the exact domain once DNS is live.

---

## Summary — What's Done vs. What's Left

| Service | Status |
|---------|--------|
| Neon Postgres | ✅ Connection string in hand |
| API key rotation (OpenAI, Gemini, EXA, Tavily) | ❌ Rotate now |
| Fly.io app created + secrets set | ❌ Pending |
| Object storage (Tigris or R2) | ❌ Pending |
| Google OAuth production client | ❌ Pending |
| Domain & DNS | ❌ Pending |
