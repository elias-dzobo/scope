# CI Merge Gates Guide

## Why We Use CI Merge Gates
CI merge gates protect `main` from regressions.

For this stock-research system, gates are critical because one shape mismatch can silently break downstream stages (`query -> search -> filter -> scrape -> artifacts`).

Primary goals:
- Keep the pipeline always mergeable and runnable.
- Catch contract drift early (before manual QA).
- Prevent bugs like partial traversal or changed payload shapes.
- Make reviews faster by automating objective checks.

## What Was Implemented
- Workflow: `.github/workflows/ci.yml`
- Pytest discovery config: `pytest.ini`
- Deterministic contract/integration tests:
  - `tests/test_schema_contracts.py`
  - `tests/test_pipeline_shapes.py`
  - `tests/test_pipeline_orchestration.py`
- Fixture for canonical query-plan shape:
  - `tests/fixtures/query_plan.json`

## What The Gate Checks
Every PR/push triggers:
1. Dependency install with `uv`.
2. Compile check (`python -m compileall`).
3. Deterministic tests for schema + stage contracts + orchestration.

If any step fails, the CI job fails and merge should be blocked.

## How To Run The Same Gates Locally
From repo root:

```bash
uv sync --frozen
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python -m compileall src main.py
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python -m pytest tests/test_*.py
```

Use these commands before opening a PR.

## How To Enforce Merge Blocking in GitHub
1. Go to `Settings -> Branches`.
2. Add a branch protection rule for `main`.
3. Enable `Require status checks to pass before merging`.
4. Select the check: `test-and-contract-gates`.
5. Optional but recommended: enable `Require branches to be up to date before merging`.

After this, PRs cannot merge unless CI is green.

## When To Add New Gates
Add a new gate when you add risk in one of these areas:
- New stage I/O contract.
- New scoring or ranking logic.
- New parser/extractor behavior.
- New data source adapters.

Pattern to follow:
1. Add/update fixture representing expected shape.
2. Add failing test first.
3. Implement code.
4. Ensure CI passes.

## Practical Gate Design Rules
- Keep gates deterministic (no network/LLM calls).
- Mock external systems and test internal contracts.
- Keep tests fast so engineers trust and run them often.
- Fail loud on schema drift.

## Current Scope and Next Upgrade
Current gates cover:
- Schema contract validation.
- Search/filter/scrape shape invariants.
- Pipeline stage checkpoint/artifact production.

Next recommended upgrades:
1. Add `ruff` and type checks as additional gates.
2. Add fixture-based scoring tests once scoring engine is implemented.
3. Add evaluation regression thresholds (precision/recall) as a release gate.
