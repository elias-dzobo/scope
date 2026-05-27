# Fly.io Deployment Notes

Fly.io can work well for Scope if we treat deep research as background work, not as long-lived HTTP work.

## Recommended Shape

- Run the API and worker as separate Fly process groups from the same Docker image.
- Keep `web` as the only public HTTP process.
- Run `worker` without an HTTP service; it leases jobs from Postgres.
- Use `RESEARCH_EXECUTION_BACKEND=durable` in production so API requests only create queued jobs.
- Keep at least one API Machine running for responsiveness.
- Keep at least one worker Machine running while paid/private-beta users can start research.

Fly supports process groups in `fly.toml`, and each group runs in its own Machine set. That lets us scale API and worker capacity independently.

## Deep Research Implications

Deep research can take minutes and can involve provider retries, document fetching, and parsing. On Fly:

- Do not execute research inside request handlers.
- Do not depend on Machine-local filesystem state for durable results.
- Store run state in Postgres.
- Store only durable final artifacts in S3-compatible object storage.
- Treat raw PDFs, parsed documents, table JSON, and trace-like extraction payloads as temporary working files.
- Let workers recover stale leases after restarts.

## Suggested Services

- API/worker: Fly app using `infra/fly/fly.toml.example`.
- Database: Neon Postgres, Fly Managed Postgres, Supabase, or another managed Postgres.
- Artifacts: Tigris, R2, AWS S3, or MinIO outside Fly if self-hosted.
- Frontend: static hosting, another Fly app, or a CDN-backed static host.

## Neon Postgres

Neon is a good fit for Scope on Fly because it gives us managed Postgres without running a database Machine ourselves. Scope should continue treating Postgres as the source of truth for:

- users and sessions;
- onboarding profiles and profile snapshots;
- research runs, run events, leases, and worker heartbeat state;
- advisor conversations and messages;
- memory graph nodes, edges, and chunks;
- artifact manifest records;
- usage ledger, credit reservations, and entitlements.

Do not put the Neon connection string directly in `fly.toml` or in the repo. Set it as a Fly secret:

```bash
fly secrets set \
  SCOPE_DB_BACKEND=postgres \
  DATABASE_URL='postgresql://USER:PASSWORD@HOST/neondb?sslmode=require'
```

Use the pooled Neon connection string for normal API traffic if Neon provides one for the project. Use the direct connection string for migrations if the pooled endpoint does not support the migration behavior we need. If we split them later, the app can keep `DATABASE_URL` for runtime and add a separate `MIGRATION_DATABASE_URL` for release commands.

Security notes:

- Rotate the database password if it has ever been pasted into chat, committed, logged, or shared.
- Keep `sslmode=require`.
- Use a least-privilege app database role before public launch instead of the project owner role.
- Keep migrations in Fly `release_command` so schema changes run before new Machines start serving traffic.

## Artifact Retention

Scope does not need to keep every intermediate research artifact forever. The product needs the final synthesis, source/evidence records, profile snapshot, and enough audit metadata to explain the output. Raw PDFs and parsed extraction files are mainly useful while a run is active or while debugging a failed run.

Recommended Fly settings:

```env
ARTIFACT_RETENTION_MODE=ephemeral
ARTIFACT_KEEP_TYPES=final_synthesis,document_evidence
ARTIFACT_TEMP_TTL_HOURS=72
ARTIFACT_KEEP_FAILED_RUNS_DAYS=14
ARTIFACT_KEEP_RAW_DOCUMENTS=false
```

With this mode:

- the worker can write raw/parsed artifacts during execution;
- after a successful run, non-durable artifacts are deleted;
- only durable artifact records are saved to `artifact_manifest`;
- failed runs can retain local working files until a later TTL cleanup job.

On Fly, do not rely on Machine disk for permanent research state. Machine disk is fine for working files, but durable results should live in Postgres and object storage.

## First Deploy Outline

1. Copy `infra/fly/fly.toml.example` to `fly.toml`.
2. Create the Fly app.
3. Set secrets:
   - `DATABASE_URL`
   - `SCOPE_DB_BACKEND=postgres`
   - `JWT_SECRET`
   - `GOOGLE_CLIENT_ID`
   - provider keys
   - S3 artifact credentials
   - `SCOPE_ALLOWED_ORIGINS`
   - artifact retention settings, unless using the defaults in `fly.toml`
4. Deploy with `fly deploy`.
5. Scale workers:
   - `fly scale count web=1 worker=1`
6. Run smoke checks:
   - `/health`
   - Google sign-in
   - onboarding save
   - research start/status/result
   - advisor resume

## Scaling

- Scale API Machines based on request load.
- Scale worker Machines based on queue depth and average job duration.
- Increase worker memory before increasing concurrency if PDF/document parsing becomes memory-heavy.
- Keep `RESEARCH_MAX_ACTIVE_RUNS_PER_USER` and credit limits enabled so a small number of users cannot exhaust provider budgets.

## Pricing Fit

The hybrid subscription-plus-credit model maps cleanly to this deployment:

- API estimates/reserves credits before queueing expensive work.
- Worker consumes the budget snapshot while executing.
- Usage ledger records final charge, refund, or failed-run treatment.

The current implementation adds the durable worker and database tables needed for that model; Stripe integration can sit on top later.
