# Advisor Agent Evals

This directory contains the Scope advisor agent eval harness.

The harness uses Scope-specific fixtures and deterministic graders for product
behavior, while exposing the same runs as DeepEval `LLMTestCase` objects when
DeepEval is installed.

Run:

```bash
uv run python evaluations/advisor/deepeval_runner.py
```

Default report:

```text
storage/evals/advisor_eval_report.json
```

Current deterministic metrics:

- `plan_intent_match`
- `ticker_match`
- `theme_match`
- `coverage_decision_match`
- `freshness_decision_match`
- `trace_path_match`
- `stance_match`
- `evidence_refs_present`
- `context_pack_present`
- `memory_write_match`
- `privacy_no_raw_onboarding`

DeepEval layer:

- The runner imports `deepeval.test_case.LLMTestCase` when available.
- Scope run outputs, traces, plans, and coverage reports are converted into
  DeepEval test cases.
- LLM-as-judge metrics can be added on top of those cases without changing the
  Scope fixtures.

The first suite uses fake company and generic research tools. That keeps evals
cheap, deterministic, and appropriate for CI. A separate live eval suite can be
added later for model/provider regressions.
