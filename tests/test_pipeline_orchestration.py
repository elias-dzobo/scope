import json

from research_core.legacy_pipeline import main as pipeline


def test_stock_research_pipeline_writes_stage_checkpoints_and_artifacts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def fake_generate_search_queries(stock_name):
        return {
            "stock": stock_name,
            "pillars": [
                {
                    "pillar_name": "Valuation",
                    "objective": "test",
                    "queries": [{"query": "q1", "intent": "i1"}],
                }
            ],
        }

    def fake_execute_search_queries(_query_plan, progress_callback=None):
        if progress_callback:
            progress_callback("03_search", {"status": "running", "stage_progress": 100, "current_substep": "search query 1/1"})
        return {
            "Valuation": [
                {
                    "query": "q1",
                    "intent": "i1",
                    "query_duration_seconds": 0.123,
                    "provider": "google",
                    "results": [{"title": "A", "link": "https://a.com", "snippet": "s1"}],
                }
            ]
        }

    def fake_discover_primary_sources(_stock_name, ticker="", progress_callback=None):
        if progress_callback:
            progress_callback("02b_primary_source_discovery", {"status": "running", "stage_progress": 100, "current_substep": "primary discovery query 1/1"})
        return {
            "Valuation": [
                {
                    "title": "IR Annual Report",
                    "link": "https://ir.acme.com/annual-report.pdf",
                    "snippet": "Annual report",
                    "is_primary_source": True,
                    "document_type": "pdf",
                    "discovery_type": "annual_report",
                }
            ]
        }

    def fake_filter_results(_stock_name, _search_results, ticker="", progress_callback=None):
        if progress_callback:
            progress_callback("04_filter", {"status": "running", "stage_progress": 100, "current_substep": "judging result 1/1"})
        return {
            "Valuation": [{"title": "A", "link": "https://a.com", "snippet": "s1", "is_primary_source": True, "document_type": "pdf"}],
        }

    def fake_execute_site_scrape(_filtered_results, progress_callback=None):
        if progress_callback:
            progress_callback("05_scrape", {"status": "running", "stage_progress": 100, "current_substep": "scraping document 1/1"})
        return {
            "Valuation": [
                {
                    "title": "A title",
                    "body": "A body",
                    "scrape_method": "fast_http",
                    "scrape_duration_seconds": 0.456,
                    "document_type": "pdf",
                    "is_primary_source": True,
                    "character_count": 6,
                }
            ],
        }

    def fake_extract_evidence_facts(_scraped_results, stock_name="", ticker="", llm_enabled=None, progress_callback=None):
        if progress_callback:
            progress_callback("07_extract", {"status": "running", "stage_progress": 100, "current_substep": "extracting pillar 1/1"})
        return {
            "Valuation": [
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Multiples",
                    "source_title": "A title",
                    "metric_name": "P/E",
                    "metric_value": "10x",
                    "period": "TTM",
                    "excerpt": "A body",
                    "confidence": 0.7,
                }
            ]
        }

    def fake_assess_pillars(_evidence_by_pillar):
        return {
            "Valuation": {
                "pillar_name": "Valuation",
                "score": 72,
                "confidence": 0.7,
                "strengths": ["Multiples: 1 evidence hits"],
                "gaps": ["Intrinsic Value"],
                "evidence_count": 1,
                "category": "Insufficient Data",
                "synopsis": "Valuation has thin data.",
                "analysis": "Valuation has thin data and weak signal coverage.",
            }
        }

    def fake_build_stock_scorecard(stock_name, ticker, _pillar_assessments, _evidence_by_pillar):
        return {
            "stock_name": stock_name,
            "ticker": ticker,
            "pillar_scores": {"Valuation": 72},
            "overall_score": 72,
            "confidence": 0.7,
            "recommendation": "Watch / Accumulate on Weakness",
            "reasoning": "Strongest pillar: Valuation. Weakest pillar: Valuation.",
            "pillar_classifications": {"Valuation": "Neutral"},
            "valuation_status": "Fairly Valued",
            "technical_state": "Neutral Trend",
            "bullish_drivers": ["Valuation: Multiples: 1 evidence hits"],
            "key_risks": ["Valuation: Intrinsic Value"],
            "invalidation_conditions": ["Financial Engine score drops below 60 on next earnings cycle."],
            "recommendation_confidence": "Medium",
        }

    monkeypatch.setattr(pipeline, "generate_search_queries", fake_generate_search_queries)
    monkeypatch.setattr(pipeline, "discover_primary_sources", fake_discover_primary_sources)
    monkeypatch.setattr(pipeline, "execute_search_queries", fake_execute_search_queries)
    monkeypatch.setattr(pipeline, "filter_results", fake_filter_results)
    monkeypatch.setattr(pipeline, "execute_site_scrape", fake_execute_site_scrape)
    monkeypatch.setattr(pipeline, "extract_evidence_facts", fake_extract_evidence_facts)
    monkeypatch.setattr(pipeline, "assess_pillars", fake_assess_pillars)
    monkeypatch.setattr(pipeline, "build_stock_scorecard", fake_build_stock_scorecard)

    summary = pipeline.stock_research_pipeline("Acme Corp", "ACME")

    assert summary["ticker"] == "ACME"
    assert summary["stock_name"] == "Acme Corp"
    assert summary["query_batch_count"] == 2
    assert summary["filtered_doc_count"] == 1
    assert summary["scraped_doc_count"] == 1
    assert summary["overall_score"] == 72
    assert summary["recommendation"] == "Watch / Accumulate on Weakness"
    assert "runtime_profile" in summary
    assert summary["runtime_profile"]["steps"]
    assert summary["runtime_profile"]["steps"][2]["stage"] == "02b_primary_source_discovery"
    assert summary["runtime_profile"]["steps"][3]["stage"] == "03_search"
    scrape_step = next(step for step in summary["runtime_profile"]["steps"] if step["stage"] == "05_scrape")
    assert scrape_step["output_summary"]["document_type_counts"]["pdf"] == 1

    state_dir = tmp_path / "storage" / "artifacts" / "ACME" / "_pipeline_state"
    expected_stage_files = [
        "01_prepare.json",
        "02_query_plan.json",
        "02b_primary_source_discovery.json",
        "03_search.json",
        "04_filter.json",
        "05_scrape.json",
        "06_persist.json",
        "07_extract.json",
        "08_assess.json",
        "09_score.json",
    ]
    for filename in expected_stage_files:
        assert (state_dir / filename).exists(), filename

    artifact_file = tmp_path / "storage" / "artifacts" / "ACME" / "Valuation.json"
    assert artifact_file.exists()

    artifact_payload = json.loads(artifact_file.read_text())
    assert artifact_payload[0]["title"] == "A title"

    scorecard_file = tmp_path / "storage" / "artifacts" / "ACME" / "analysis" / "scorecard.json"
    assert scorecard_file.exists()

    runtime_profile_file = tmp_path / "storage" / "artifacts" / "ACME" / "analysis" / "runtime_profile.json"
    assert runtime_profile_file.exists()
