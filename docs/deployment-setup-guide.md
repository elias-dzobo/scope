# Deployment Setup Guide

Step-by-step instructions for setting up every external service needed to
deploy Scope to production. Work through these sections in order — each
one produces credentials or values you'll need in the next.

---

## Prerequisites

- Node 20+ and Python 3.13+ installed locally
- `uv` installed (`pip install uv` or `brew install uv`)
- A credit card for Fly.io and Namecheap

---

## Part 1 — Buy a Domain on Namecheap

1. Go to [namecheap.com](https://namecheap.com) and search for your domain name.
2. Add it to your cart and check out. Choose **auto-renew** so it does not expire.
3. In the Namecheap dashboard, go to **Domain List → Manage** on your new domain.
4. Under **Nameservers**, switch from "Namecheap BasicDNS" to **"Custom DNS"** — you will fill in Fly's nameserver values in Part 4.
5. Note your domain (e.g. `getscope.app`). You will need it in every section below.

---

## Part 2 — Google OAuth Production Client

You need a Google OAuth client tied to your production domain.
This gives you a new `GOOGLE_CLIENT_ID` (public) and `GOOGLE_CLIENT_SECRET` (sensitive).

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Select your project (or create one: **New Project → name it "Scope"**).
3. In the left sidebar go to **APIs & Services → OAuth consent screen**.
   - Choose **External** if you want any Google user to sign in.
   - Fill in: App name (`Scope`), support email, developer contact email.
   - Under **Scopes**, add: `openid`, `email`, `profile`.
   - Save and continue through all steps.
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**.
   - Application type: **Web application**.
   - Name: `Scope Production`.
   - **Authorized JavaScript origins**: add `https://yourdomain.com` (replace with your real domain).
   - **Authorized redirect URIs**: add `https://yourdomain.com/auth/callback`.
   - Click **Create**.
5. Copy the **Client ID** and **Client Secret** — you will use both in Part 3.

> **Note:** The `GOOGLE_CLIENT_ID` (the long string ending in `.apps.googleusercontent.com`)
> is not a secret. It is safe to commit in non-sensitive config and embed in the frontend.
> The `GOOGLE_CLIENT_SECRET` must never be committed — set it only via `fly secrets set`.

---

## Part 3 — Fly.io Setup

### 3a. Install the CLI and log in

```bash
# macOS
brew install flyctl

# Authenticate
fly auth login
```

### 3b. Create the app

```bash
fly apps create scope-api
```

Open `fly.toml` and confirm `app = "scope-api"` matches (it already does).
Set your primary region — `iad` (Virginia) is a good default; choose `lhr` for Europe.

### 3c. Set Fly secrets

Run this in one pass after you have all the values from the sections above.
Replace every `<...>` with a real value.

```bash
fly secrets set \
  DATABASE_URL='postgresql://USER:PASSWORD@HOST/neondb?sslmode=require' \
  JWT_SECRET='$(openssl rand -base64 48)' \
  GOOGLE_CLIENT_ID='<client id from Part 2>' \
  GOOGLE_CLIENT_SECRET='<client secret from Part 2>' \
  OPENAI_API_KEY='<new rotated key>' \
  GEMINI_API_KEY='<new rotated key>' \
  EXA_API_KEY='<new rotated key>' \
  TAVI_API_KEY='<new rotated key>' \
  SCOPE_ALLOWED_ORIGINS='https://yourdomain.com' \
  ARTIFACT_BUCKET='scope-artifacts' \
  ARTIFACT_S3_ENDPOINT_URL='<tigris endpoint from Part 4>' \
  AWS_ACCESS_KEY_ID='<tigris key from Part 4>' \
  AWS_SECRET_ACCESS_KEY='<tigris secret from Part 4>'
```

To generate a strong JWT secret without copy-pasting a random string:
```bash
openssl rand -base64 48
```
Copy the output and paste it as the `JWT_SECRET` value.

Verify all secrets are registered:
```bash
fly secrets list
```

---

## Part 4 — Tigris Object Storage (Artifact Bucket)

Tigris is Fly's native S3-compatible storage. No separate account needed.

### 4a. Create a Tigris bucket

```bash
fly storage create
```

When prompted:
- Name the bucket `scope-artifacts`.
- Select the same region as your Fly app.

Fly prints output like:
```
AWS_ENDPOINT_URL_S3 = https://fly.storage.tigris.dev
AWS_ACCESS_KEY_ID   = tid_xxxx
AWS_SECRET_ACCESS_KEY = tsec_xxxx
BUCKET_NAME         = scope-artifacts
```

Copy all four values. You need them for the `fly secrets set` command in Part 3c.

### 4b. Confirm the bucket is private

Tigris buckets are private by default. No extra step needed, but double-check:
```bash
fly storage list
```
The bucket should show no public access policy.

---

## Part 5 — Domain & DNS on Fly

### 5a. Add your domain to your Fly app

```bash
fly certs add yourdomain.com
fly certs add www.yourdomain.com
```

Fly will print DNS records you need to add. They look like:

```
Type  Host  Value
A     @     66.241.124.x
AAAA  @     2a09:8280:1::x:xxxx
CNAME www   yourdomain.com.
```

### 5b. Add DNS records in Namecheap

1. In Namecheap dashboard go to **Domain List → Manage → Advanced DNS**.
2. Delete any existing `A` or `CNAME` records for `@` and `www`.
3. Add the records Fly gave you:
   - **A Record**: Host `@`, Value = Fly IPv4, TTL Automatic
   - **AAAA Record**: Host `@`, Value = Fly IPv6, TTL Automatic
   - **CNAME Record**: Host `www`, Value = `yourdomain.com.` (trailing dot included), TTL Automatic
4. Save all changes.

DNS propagation takes 5–30 minutes. Check it with:
```bash
dig yourdomain.com A
```

### 5c. Verify TLS is issued

```bash
fly certs show yourdomain.com
```

Wait until the status shows `Issued`. Then test:
```bash
curl -I https://yourdomain.com/health
```

Expected: `HTTP/2 200`.

---

## Part 6 — First Deploy

Once all secrets are set and DNS is live:

### 6a. Build and deploy

```bash
fly deploy
```

This will:
1. Build the Docker image.
2. Run `python scripts/migrate_db.py` as the release command (runs Alembic migrations against Neon before any machine starts).
3. Start `web` and `worker` machines.

### 6b. Confirm both processes started

```bash
fly status
```

You should see one `web` machine and one `worker` machine both in `started` state.

### 6c. Check logs for startup errors

```bash
fly logs
```

Look for any exceptions during startup. The API should print something like:
```
INFO: Application startup complete.
```

### 6d. Health check

```bash
curl https://yourdomain.com/health
curl https://yourdomain.com/api/v1/health
```

Both should return `{"status": "ok", ...}`.

### 6e. Scale to at least one of each

```bash
fly scale count web=1 worker=1
```

---

## Part 7 — Smoke Test

Run through these manually in the browser after deploy:

- [ ] Go to `https://yourdomain.com` — landing page loads.
- [ ] Click **Sign in with Google** — Google consent screen appears.
- [ ] Sign in — redirected back to the app, session starts.
- [ ] Complete onboarding — profile saved.
- [ ] Start a research run — run appears in queue.
- [ ] Wait for completion — results render with evidence citations.
- [ ] Open the advisor on a completed run — conversation starts.
- [ ] Ask a follow-up question — stays in thread context.
- [ ] Go to saved research library — previous runs listed.
- [ ] Sign out — session cleared, redirected to landing page.

---

## Part 8 — Update Google OAuth for Production Domain

After DNS is live and the app is deployed:

1. Go back to **Google Cloud Console → APIs & Services → Credentials**.
2. Click your `Scope Production` OAuth client.
3. Confirm the **Authorized JavaScript origins** and **Authorized redirect URIs** match your exact deployed domain (e.g. `https://getscope.app`, not `http://` and not with a trailing slash).
4. If you used a `www` subdomain as the canonical URL, add both `https://getscope.app` and `https://www.getscope.app` as origins.
5. Save. Changes take a few minutes to propagate.

---

## Quick Reference — All Secrets

| Secret | Where it comes from | Sensitive? |
|--------|---------------------|------------|
| `DATABASE_URL` | Neon dashboard → Connection string (pooled) | ✅ Yes |
| `JWT_SECRET` | `openssl rand -base64 48` | ✅ Yes |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → Credentials | No — public identifier |
| `GOOGLE_CLIENT_SECRET` | Google Cloud Console → Credentials | ✅ Yes |
| `OPENAI_API_KEY` | platform.openai.com | ✅ Yes |
| `GEMINI_API_KEY` | aistudio.google.com | ✅ Yes |
| `EXA_API_KEY` | exa.ai dashboard | ✅ Yes |
| `TAVI_API_KEY` | tavily.com dashboard | ✅ Yes |
| `ARTIFACT_S3_ENDPOINT_URL` | `fly storage create` output | No |
| `AWS_ACCESS_KEY_ID` | `fly storage create` output | ✅ Yes |
| `AWS_SECRET_ACCESS_KEY` | `fly storage create` output | ✅ Yes |
| `SCOPE_ALLOWED_ORIGINS` | Your domain name | No |

---

## Troubleshooting

**Google sign-in returns 401**
- `GOOGLE_CLIENT_ID` in Fly secrets does not match the one in your Google OAuth client.
- The production domain is not listed in the OAuth client's authorized JavaScript origins.
- The token from the frontend was issued for a different client ID.

**`fly deploy` fails at release command**
- Neon `DATABASE_URL` secret is not set or the password is wrong.
- Run `fly ssh console -C "uv run python scripts/validate_migrations.py"` to debug.

**Workers not picking up jobs**
- `RESEARCH_EXECUTION_BACKEND=durable` must be set (it is in `fly.toml` env).
- Check worker logs: `fly logs --process-group worker`.

**TLS certificate not issued**
- DNS records have not propagated yet. Wait 10 minutes and re-run `fly certs show yourdomain.com`.
- Make sure the `A` and `AAAA` records in Namecheap point to the exact IPs Fly gave you (no extra spaces).

**OTel exporter errors in logs (`StatusCode.UNAVAILABLE`)**
- Expected locally if no OTel collector is running on `localhost:4317`.
- On Fly these can be silenced by setting `OBSERVABILITY_ENABLED=false` until you set up Grafana.
- These errors do not affect the API — they are non-fatal background retries.
