# MCP Server Setup

Claude Code MCP servers are configured at two levels:

| Level | File | Scope |
|---|---|---|
| Global | `~/.claude/settings.json` | All projects — Fly.io, RevenueCat, Neon, Supabase, Stripe, App Store Connect |
| Project | `.claude/settings.json` | Scope only — direct Postgres connection |

---

## Global servers (`~/.claude/settings.json`)

### Fly.io
Uses your local `flyctl` CLI — no key needed, just stay logged in.

```bash
fly auth login          # one-time, stays authenticated
```

`flyctl` is already installed at `/usr/local/bin/fly`.

---

### RevenueCat
Needs a RevenueCat API v2 key (project-level, not legacy V1 key).

1. Dashboard → **API Keys** → **+ New key** → choose **Full access** or read-only
2. Store the key:

```bash
# local dev — add to ~/.zshrc or ~/.zshenv
export REVENUECAT_API_KEY="rcv2_..."
```

Claude Code reads `${REVENUECAT_API_KEY}` from your shell environment at startup.

---

### Neon
Needs a Neon personal access token (not a project-specific connection string — that's for Postgres below).

1. [console.neon.tech](https://console.neon.tech) → top-right avatar → **Account settings** → **API keys** → **Generate new API key**
2. Store the key:

```bash
export NEON_API_KEY="neon_..."
```

---

### Supabase
Needs a Supabase personal access token (not a project anon/service key).

1. [supabase.com/dashboard](https://supabase.com/dashboard) → **Account** → **Access Tokens** → **Generate new token**
2. Store the token:

```bash
export SUPABASE_ACCESS_TOKEN="sbp_..."
```

---

### Stripe
Stripe's MCP server uses OAuth — no key goes in the config. Claude Code will open a browser flow to authorize on first use.

No local setup required.

---

### App Store Connect
Needs an App Store Connect API key (not an Apple ID password).

1. [appstoreconnect.apple.com](https://appstoreconnect.apple.com) → **Users and Access** → **Keys** tab → **+** → choose role (Admin recommended)
2. Download the `.p8` file — **Apple only shows it once**
3. Note the **Key ID** (10-char alphanumeric) and **Issuer ID** (UUID shown at top of the Keys page)
4. Store the `.p8` somewhere permanent, e.g. `~/.appstore/AuthKey_XXXXXXXXXX.p8`
5. Export the three vars:

```bash
export APP_STORE_KEY_ID="XXXXXXXXXX"
export APP_STORE_ISSUER_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export APP_STORE_P8_PATH="$HOME/.appstore/AuthKey_XXXXXXXXXX.p8"
```

The `@trialanderror-ai/appstore-connect-mcp` package covers all 923 App Store Connect API endpoints and is installed on first use via `npx`.

---

## Project server (`.claude/settings.json`)

### Postgres (Scope / Neon DB)
Connects directly to the Scope Neon database — lets Claude query tables, inspect schema, and run read queries during development.

Set `DATABASE_URL` in your local `.env` (it's already listed in `.env.example`):

```bash
# .env  (gitignored — never commit)
DATABASE_URL=postgresql://scope_owner:<password>@<host>.neon.tech/scope?sslmode=require
```

Find the exact connection string in [console.neon.tech](https://console.neon.tech) → your project → **Connection Details** → select the branch → copy the **Pooled connection string** for development, or the direct string if you need `LISTEN/NOTIFY`.

The `@modelcontextprotocol/server-postgres` package is installed on first use via `npx`.

> **Security:** The Postgres MCP server runs locally and connects over TLS — your DB password never leaves your machine. Do not set `DATABASE_URL` in global shell env (only `.env`) to avoid leaking it to unrelated projects.

---

## Making env vars available to Claude Code

Claude Code reads `${VAR}` references from the environment it's launched in. The cleanest approach:

```bash
# ~/.zshrc  (or ~/.zshenv for non-interactive shells)

# MCP keys — safe to export globally (no project data, just API access)
export REVENUECAT_API_KEY="rcv2_..."
export NEON_API_KEY="neon_..."
export SUPABASE_ACCESS_TOKEN="sbp_..."
export APP_STORE_KEY_ID="XXXXXXXXXX"
export APP_STORE_ISSUER_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export APP_STORE_P8_PATH="$HOME/.appstore/AuthKey_XXXXXXXXXX.p8"
```

`DATABASE_URL` stays in the project `.env` only and is sourced by `uv run` / the dev server automatically.

---

## Production secrets

| Service | Where to set |
|---|---|
| DATABASE_URL | `fly secrets set DATABASE_URL="..."` |
| All API keys that backend code needs | `fly secrets set KEY="..."` |
| MCP-only keys (RevenueCat, Neon, etc.) | Local `~/.zshrc` only — not needed on Fly |

MCP servers run on your local machine only; they never run on Fly.io.

---

## Verify the servers loaded

After restarting Claude Code (or opening a new session), run:

```
/mcp
```

You should see all configured servers listed with their status. If a server shows an error, check that its required env var is exported and non-empty.
