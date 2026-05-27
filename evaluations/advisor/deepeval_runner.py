"""Run advisor agent evals with a DeepEval-compatible harness.

DeepEval is optional for local development. The runner always executes Scope's
deterministic product metrics. When DeepEval is installed, each run is also
converted into `LLMTestCase` objects so LLM-as-judge metrics can be added
without changing fixtures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scope_api.advisor import AdvisorRunRequest, run_advisor_harness

from evaluations.advisor.scope_metrics import grade_run, summarize_results
from evaluations.advisor.scope_setup import fake_tools_for_case, setup_case


DEFAULT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "advisor_eval_cases.jsonl"
DEFAULT_REPORT_PATH = Path("storage") / "evals" / "advisor_eval_report.json"


def load_cases(path: Path = DEFAULT_FIXTURE_PATH) -> list[dict[str, Any]]:
    """Load JSONL advisor eval cases."""
    cases = []
    for line in path.read_text().splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one eval case against the advisor harness."""
    env = setup_case(case)
    try:
        tools = fake_tools_for_case(case)
        run = run_advisor_harness(
            user_id=env.user["id"],
            payload=AdvisorRunRequest(
                query=case["query"],
                allowDeepResearch=bool(case.get("allowDeepResearch", False)),
                mode=case.get("mode", "auto"),
            ),
            research_controller=tools["research_controller"],
            generic_harness=tools["generic_harness"],
        )
        metric_results = grade_run(run, case.get("expected", {}), env.user["id"])
        metrics = [metric.__dict__ for metric in metric_results]
        return {
            "id": case["id"],
            "query": case["query"],
            "passed": all(metric["passed"] for metric in metrics),
            "metrics": metrics,
            "run": run,
        }
    finally:
        env.cleanup()


def build_deepeval_test_cases(case_results: list[dict[str, Any]]) -> list[Any]:
    """Convert Scope eval results into DeepEval test cases when available."""
    try:
        from deepeval.test_case import LLMTestCase
    except ImportError:
        return []

    test_cases = []
    for result in case_results:
        test_cases.append(
            LLMTestCase(
                input=result["query"],
                actual_output=json.dumps(result["run"].get("answer", {}), indent=2),
                expected_output=json.dumps(
                    {
                        "expectedMetrics": {
                            metric["name"]: metric["passed"]
                            for metric in result["metrics"]
                        }
                    },
                    indent=2,
                ),
                context=[
                    json.dumps(
                        {
                            "plan": result["run"].get("plan", {}),
                            "coverage": result["run"].get("coverage", {}),
                            "trace": result["run"].get("trace", []),
                        },
                        indent=2,
                    )
                ],
            )
        )
    return test_cases


def run_all(cases_path: Path = DEFAULT_FIXTURE_PATH, report_path: Path = DEFAULT_REPORT_PATH) -> dict[str, Any]:
    """Run all advisor eval cases and persist a JSON report."""
    case_results = [run_case(case) for case in load_cases(cases_path)]
    report = {
        "framework": {
            "name": "deepeval",
            "available": bool(build_deepeval_test_cases(case_results)),
            "note": "Scope deterministic metrics always run; DeepEval test cases are built when deepeval is installed.",
        },
        "summary": summarize_results(case_results),
        "cases": case_results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    """CLI entrypoint for advisor evals."""
    parser = argparse.ArgumentParser(description="Run Scope advisor agent evals.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()

    report = run_all(cases_path=args.cases, report_path=args.report)
    summary = report["summary"]
    print(
        f"Advisor evals: {summary['passed']}/{summary['total']} passed "
        f"({summary['failed']} failed). Report: {args.report}"
    )
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
