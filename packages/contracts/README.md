# Scope API Contracts

This package owns shared contracts between `apps/api` and `apps/web`.

- `openapi/openapi.json` is exported from the FastAPI app.
- `generated/typescript` is reserved for generated frontend types.
- During the compatibility migration, `apps/web/src/types/api.ts` remains the additive hand-maintained UI contract.
