# Security Audit

Date: 2026-05-12

This audit reviews Scope's current security posture for a VPS-hosted production
deployment. It focuses on authentication, authorization, secret handling,
browser/API boundaries, data protection, dependency risk, and AI-specific risks.

## Executive Summary

Scope has a reasonable authentication foundation with Google sign-in and signed
JWTs, but it needs production hardening before public users:

- Restrict CORS.
- Require auth for all user-owned research routes.
- Stop storing JWTs in unsafe browser storage if currently using local storage.
- Add strict production env validation.
- Add CSRF-aware cookie session or short-lived access token + refresh strategy.
- Protect MinIO and Postgres from public exposure.
- Add prompt-injection and data-leakage guardrails to advisor and research agents.

## Critical Findings

### SEC-001: Wildcard CORS

Severity: Critical

Location:

- `apps/api/src/scope_api/app.py`

Risk:

Any website can call the API from a browser if a valid user token is available
in that browser context. This increases exposure to token theft and confused
deputy patterns.

Fix:

- Add `SCOPE_ALLOWED_ORIGINS`.
- In production, allow only the deployed frontend origin.

Acceptance:

- Unknown origins receive no browser CORS permission.

### SEC-002: Optional Auth On User-Owned Research Routes

Severity: Critical

Risk:

The API supports anonymous run behavior for legacy compatibility. In production,
user-owned workflows should require authentication.

Fix:

- Add `SCOPE_REQUIRE_AUTH=true`.
- Require current user for:
  - research creation
  - research listing
  - research result fetch
  - artifact listing
  - memory routes
  - advisor routes
  - onboarding routes

Acceptance:

- Anonymous production request returns `401`.

### SEC-003: JWT Session Model Needs Production Strategy

Severity: High

Current:

- Custom HS256 JWT helper.
- Logout is client-managed.

Risks:

- No server-side revocation.
- Long-lived token default is 24 hours.
- If frontend stores token in local storage, XSS can steal it.

Recommended:

- Move to `HttpOnly`, `Secure`, `SameSite=Lax` cookies for refresh/session.
- Use short-lived access tokens.
- Add server-side refresh token rotation or session table.
- Keep API bearer tokens only for internal service/API usage.

Acceptance:

- Logout invalidates server session.
- Stolen old refresh token cannot be reused after rotation.

### SEC-004: Dev Google Token Escape Hatch

Severity: High

Current:

`AUTH_ALLOW_DEV_GOOGLE_TOKEN=true` allows JSON credentials.

Production requirement:

- Must be false in production.
- Add startup validation that refuses production boot if enabled.

Acceptance:

- Production boot fails when `AUTH_ALLOW_DEV_GOOGLE_TOKEN=true`.

### SEC-005: Public Object Storage Risk

Severity: High

Risk:

Research artifacts may include PDFs, extracted financial tables, raw document
text, and generated analysis. Buckets must not be publicly readable.

Fix:

- Keep MinIO bound to `127.0.0.1`.
- No public bucket policy.
- Use signed URL flow later if needed.
- Encrypt disks or volume where feasible.

Acceptance:

- `curl http://server:9000` from outside cannot access MinIO.
- Bucket anonymous read is disabled.

## Important Findings

### SEC-006: Secrets Must Stay Outside Repo

Required:

- No `.env` committed.
- Use `/etc/scope/scope.env` on VPS.
- Restrict permissions:

```bash
sudo chown root:scope /etc/scope/scope.env
sudo chmod 640 /etc/scope/scope.env
```

### SEC-007: Provider API Key Blast Radius

Risks:

- LLM/search provider keys can incur cost.
- Browser rendering tokens can be abused.

Fix:

- Put provider budgets/rate limits in provider dashboards.
- Use separate keys for dev/staging/prod.
- Rotate keys on deploy handoff.

### SEC-008: Prompt Injection And Source Poisoning

Risk:

The research system ingests web pages, PDFs, filings, and grounded search
content. Those documents may contain adversarial instructions.

Guardrails required:

- Treat external documents as untrusted data.
- Never allow document text to override system/developer policies.
- Evidence judge must reject unrelated companies/documents.
- Advisor must cite saved/fresh evidence for factual claims.
- Raw onboarding answers must never go to advisor context.

### SEC-009: Financial Advice Framing

Risk:

The product discusses investments and personalized suitability.

Required guardrails:

- Avoid guaranteed-return language.
- Separate company quality from investor fit.
- Include uncertainty and evidence limits.
- Make clear output is research support, not regulated financial advice.
- Add legal copy before public launch.

### SEC-010: Dependency And Supply Chain

Required:

- Pin Python dependencies through `uv.lock`.
- Use `npm ci` for web.
- Add vulnerability scanning:

```bash
uv run python -m pip-audit
npm audit --omit=dev
```

Current note:

`@nonfungibledev/horus-cli` exists in web dependencies. Confirm it is required
for production. If not needed at runtime, move/remove it.

## Infrastructure Security Checklist

- SSH key login only.
- Disable password SSH.
- UFW allows only `22`, `80`, `443`.
- Postgres bound to localhost/private network.
- MinIO bound to localhost/private network.
- TLS via Caddy or Nginx + Certbot.
- Security headers on frontend:
  - `Strict-Transport-Security`
  - `X-Content-Type-Options`
  - `Referrer-Policy`
  - `Content-Security-Policy`
- API request body size limit.
- Nginx/Caddy rate limit on auth and research-create endpoints.

## Security Go/No-Go

No public launch until:

- CORS restricted.
- Production auth required.
- Dev token mode blocked.
- JWT/session strategy hardened.
- MinIO private.
- Postgres private.
- Secrets externalized.
- Provider keys rotated.
- Backup encryption/permissions reviewed.
- Legal/financial disclaimer added.

