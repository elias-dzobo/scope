from research_core.harness import ResearchPlanner, ResearchWorkstream, WorkstreamStatus
from research_core.harness.models import ResearchMode


def test_company_planner_creates_flexible_six_pillar_plan():
    planner = ResearchPlanner()
    brief = planner.create_company_brief("Eli Lilly", "lly")
    plan = planner.create_plan(brief)

    assert plan.mode == ResearchMode.COMPANY_EQUITY_RESEARCH
    assert plan.entities[0].ticker == "LLY"
    assert len(plan.workstreams) == 6
    assert plan.workstreams[0].id == "macro_industry"
    assert plan.workstreams[0].required_evidence
    assert plan.stop_conditions.max_plan_iterations == 4


def test_company_planner_respects_selected_pillars():
    planner = ResearchPlanner()
    brief = planner.create_company_brief("Eli Lilly", "LLY", selected_pillars=["Valuation"])
    plan = planner.create_plan(brief)

    assert [workstream.title for workstream in plan.workstreams] == ["Valuation"]
    assert plan.workstreams[0].pillar_name == "Valuation"


def test_workstream_records_completed_steps_without_duplicates():
    workstream = ResearchWorkstream(id="valuation", title="Valuation", goal="Assess valuation")

    workstream.mark_step_completed("search")
    workstream.mark_step_completed("search")

    assert workstream.status == WorkstreamStatus.PENDING
    assert workstream.completed_steps == ["search"]
