from scope_api import db
from scope_api.app import (
    create_research_run,
    get_research_run,
    get_research_run_results,
    list_research_runs,
)
from scope_api.auth.models import AuthUser
from scope_api.schemas import StartResearchRunRequest


def test_create_run_handler_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()

    captured = {}

    def fake_start(company_name, ticker, selected_pillars, user_id=None):
        captured["user_id"] = user_id
        return "run-123"

    monkeypatch.setattr("scope_api.app.start_research_run", fake_start)
    payload = StartResearchRunRequest(
        company_name="Apple Inc.",
        ticker="AAPL",
        selected_pillars=["Valuation", "Financial Engine"],
    )

    response = create_research_run(payload, current_user=AuthUser("user-1", "e@example.com", "E", ""))
    assert response.run_id == "run-123"
    assert response.status == "queued"
    assert captured["user_id"] == "user-1"


def test_get_run_and_results_handler_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    db.create_run("run-abc", "Apple Inc.", "AAPL", ["Valuation"])
    db.update_run_state(
        "run-abc",
        status="completed",
        current_stage="completed",
        progress=100,
        summary={"ticker": "AAPL"},
        result={"summary": {"ticker": "AAPL"}, "generatedAt": "2026-01-01T00:00:00+00:00"},
        completed=True,
    )

    run_response = get_research_run("run-abc")
    list_response = list_research_runs(limit=5)
    result_response = get_research_run_results("run-abc")

    assert run_response.id == "run-abc"
    assert run_response.status == "completed"
    assert run_response.summary["ticker"] == "AAPL"
    assert run_response.current_substep == ""
    assert run_response.stage_progress == 0

    assert len(list_response) == 1
    assert list_response[0].id == "run-abc"

    assert result_response.id == "run-abc"
    assert result_response.status == "completed"
    assert result_response.result["summary"]["ticker"] == "AAPL"


def test_research_run_ownership_scopes_lists_and_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="owner@example.com", google_sub="g-1", display_name="Owner")
    other = db.upsert_user(email="other@example.com", google_sub="g-2", display_name="Other")
    db.create_run("anon-run", "Apple Inc.", "AAPL", ["Valuation"])
    db.create_run("owned-run", "Microsoft", "MSFT", ["Valuation"], user_id=user["id"])
    db.create_run("other-run", "Tesla", "TSLA", ["Valuation"], user_id=other["id"])

    authed = AuthUser.from_row(user)
    anonymous_runs = list_research_runs(limit=10)
    owner_runs = list_research_runs(limit=10, current_user=authed)

    assert [item.id for item in anonymous_runs] == ["anon-run"]
    assert [item.id for item in owner_runs] == ["owned-run"]
    assert get_research_run("owned-run", current_user=authed).id == "owned-run"

    try:
        get_research_run("other-run", current_user=authed)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("Expected cross-user run lookup to be hidden")


def test_research_run_list_filters_preserve_legacy_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    db.create_run("run-aapl", "Apple Inc.", "AAPL", ["Valuation"])
    db.update_run_state("run-aapl", status="completed", completed=True, summary={"ticker": "AAPL", "recommendation": "Buy"})
    db.create_run("run-msft", "Microsoft", "MSFT", ["Valuation"])
    db.update_run_state("run-msft", status="failed", error_message="boom")

    completed = list_research_runs(limit=10, status="completed")
    ticker = list_research_runs(limit=10, ticker="MSFT")
    searched = list_research_runs(limit=10, q="apple")

    assert [item.id for item in completed] == ["run-aapl"]
    assert [item.id for item in ticker] == ["run-msft"]
    assert [item.id for item in searched] == ["run-aapl"]
