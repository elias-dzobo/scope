from research_core.utils import utils


def test_execute_search_queries_shape(monkeypatch):
    monkeypatch.setattr(
        utils,
        "search_tool",
        lambda q: [{"title": "Result", "link": "https://x.com", "snippet": "snippet"}],
    )

    query_plan = {
        "stock": "Acme Corp",
        "pillars": [
            {
                "pillar_name": "Valuation",
                "objective": "test",
                "queries": [
                    {"query": "q1", "intent": "i1"},
                    {"query": "q2", "intent": "i2"},
                ],
            }
        ],
    }

    out = utils.execute_search_queries(query_plan)

    assert "Valuation" in out
    assert len(out["Valuation"]) == 2
    assert out["Valuation"][0]["query"] == "q1"
    assert out["Valuation"][0]["intent"] == "i1"
    assert out["Valuation"][0]["results"][0]["link"] == "https://x.com"
    assert out["Valuation"][0]["query_duration_seconds"] >= 0
    assert out["Valuation"][0]["result_count"] == 1


def test_execute_search_queries_preserves_query_order_with_parallel_execution(monkeypatch):
    monkeypatch.setattr(
        utils,
        "search_tool",
        lambda q: [{"title": q["query"], "link": "https://x.com", "snippet": "snippet"}],
    )

    query_plan = {
        "stock": "Acme Corp",
        "pillars": [
            {
                "pillar_name": "Valuation",
                "objective": "test",
                "queries": [
                    {"query": "first query", "intent": "i1"},
                    {"query": "second query", "intent": "i2"},
                    {"query": "third query", "intent": "i3"},
                ],
            }
        ],
    }

    out = utils.execute_search_queries(query_plan)
    assert [item["query"] for item in out["Valuation"]] == ["first query", "second query", "third query"]


def test_discover_primary_sources_returns_primary_candidates(monkeypatch):
    monkeypatch.setattr(
        utils,
        "search_tool",
        lambda q: [
            {
                "title": "Acme Corp Annual Report 2025",
                "link": "https://investors.acme.com/annual-report-2025.pdf",
                "snippet": "Investor relations annual report",
            }
        ],
    )

    discovered = utils.discover_primary_sources("Acme Corp", ticker="ACME")

    assert "Financial Engine" in discovered
    assert discovered["Financial Engine"][0]["is_primary_source"] is True
    metadata = utils.get_last_primary_discovery_metadata()
    assert metadata["total_primary_sources"] >= 1


def test_merge_primary_sources_with_search_results_appends_discovery_batch():
    search_results = {"Valuation": [{"query": "q1", "intent": "i1", "results": []}]}
    primary_sources = {
        "Valuation": [
            {
                "title": "Annual Report",
                "link": "https://ir.acme.com/report.pdf",
                "snippet": "Annual report",
                "is_primary_source": True,
            }
        ]
    }

    merged = utils.merge_primary_sources_with_search_results(search_results, primary_sources)

    assert len(merged["Valuation"]) == 2
    assert merged["Valuation"][-1]["provider"] == "primary_discovery"


def test_filter_results_iterates_all_batches_and_dedupes(monkeypatch):
    calls = []

    def fake_batch_judge(pillar, stock_name, candidates):
        calls.append((pillar, [item["title"] for item in candidates]))
        output = []
        for item in candidates:
            output.append(
                {
                    "candidate_id": item["candidate_id"],
                    "is_relevant": item["title"] in {"Acme A", "Acme C"},
                    "source_trust_score": 80,
                    "summary": "ok",
                }
            )
        return output

    monkeypatch.setattr(utils, "llm_as_a_batch_judge", fake_batch_judge)

    search_results = {
        "Valuation": [
            {
                "query": "q1",
                "intent": "i1",
                "results": [
                    {"title": "Acme A", "link": "https://a.com", "snippet": "Acme valuation update"},
                    {"title": "Acme B", "link": "https://b.com", "snippet": "Acme generic snippet"},
                ],
            },
            {
                "query": "q2",
                "intent": "i2",
                "results": [
                    {"title": "Acme C", "link": "https://c.com", "snippet": "Acme multiples"},
                    {"title": "Acme A", "link": "https://a.com", "snippet": "dup"},
                ],
            },
        ]
    }

    filtered = utils.filter_results("Acme Corp", search_results)

    # Batched judging should still inspect the relevant candidates across batches.
    judged_titles = [title for _, batch in calls for title in batch]
    assert "Acme A" in judged_titles
    assert "Acme C" in judged_titles

    # Only relevant and unique links should remain
    links = [item["link"] for item in filtered["Valuation"]]
    assert links == ["https://a.com", "https://c.com"]


def test_filter_results_prioritizes_primary_source_pdfs(monkeypatch):
    monkeypatch.setattr(
        utils,
        "llm_as_a_batch_judge",
        lambda pillar, stock_name, candidates: [
            {
                "candidate_id": item["candidate_id"],
                "is_relevant": True,
                "source_trust_score": 40,
                "summary": "primary source",
            }
            for item in candidates
        ],
    )

    search_results = {
        "Financial Engine": [
            {
                "query": "q1",
                "intent": "i1",
                "results": [
                    {
                        "title": "Acme Corp 2025 Annual Report",
                        "link": "https://investors.acme.com/annual-report-2025.pdf",
                        "snippet": "Investor relations annual report",
                    }
                ],
            }
        ]
    }

    filtered = utils.filter_results("Acme Corp", search_results, ticker="ACME")

    assert len(filtered["Financial Engine"]) == 1
    assert filtered["Financial Engine"][0]["is_primary_source"] is True
    assert filtered["Financial Engine"][0]["document_type"] == "pdf"


def test_filter_results_limits_candidates_before_batch_judge(monkeypatch):
    judged_batches = []

    monkeypatch.setattr(
        utils,
        "llm_as_a_batch_judge",
        lambda pillar, stock_name, candidates: judged_batches.append(candidates) or [
            {
                "candidate_id": item["candidate_id"],
                "is_relevant": True,
                "source_trust_score": 90,
                "summary": "ok",
            }
            for item in candidates
        ],
    )

    search_results = {
        "Valuation": [
            {
                "query": "q1",
                "intent": "fair value",
                "results": [
                    {"title": f"Acme Result {i}", "link": f"https://x.com/{i}", "snippet": "Acme valuation fair value"}
                    for i in range(8)
                ],
            }
        ]
    }

    filtered = utils.filter_results("Acme Corp", search_results, ticker="ACME")

    total_judged = sum(len(batch) for batch in judged_batches)
    assert total_judged <= 3
    assert len(filtered["Valuation"]) == total_judged
    metadata = utils.get_last_filter_metadata()
    assert metadata["prepared_candidates"] == total_judged
    assert metadata["judged_candidates"] == total_judged


def test_execute_site_scrape_limits_to_top_ranked_sources(monkeypatch):
    monkeypatch.setattr(utils, "SCRAPE_TOP_PER_PILLAR", 2)
    monkeypatch.setattr(
        utils,
        "scrape_site_detailed",
        lambda url: {
            "title": f"title {url}",
            "body": "body",
            "scrape_method": "fast_http",
            "source_kind": "html",
            "content_type": "text/html",
            "scrape_duration_seconds": 0.1,
            "character_count": 4,
        },
    )

    filtered = {
        "Valuation": [
            {"title": "A", "link": "https://a.com", "source_trust_score": 60, "deterministic_score": 90, "is_primary_source": True, "host": "a.com"},
            {"title": "B", "link": "https://b.com", "source_trust_score": 50, "deterministic_score": 80, "is_primary_source": False, "host": "b.com"},
            {"title": "C", "link": "https://c.com", "source_trust_score": 40, "deterministic_score": 70, "is_primary_source": False, "host": "c.com"},
        ]
    }

    scraped = utils.execute_site_scrape(filtered)

    assert len(scraped["Valuation"]) == 2
    links = [item["link"] for item in scraped["Valuation"]]
    assert "https://a.com" in links
    assert "https://b.com" in links
    metadata = utils.get_last_scrape_metadata()
    assert metadata["planned_scrape_tasks"] == 2
    assert metadata["scrape_budget_per_pillar"] == 2
