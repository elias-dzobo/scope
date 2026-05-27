from provider_integrations.tools import main as tools


def test_search_tool_prefers_tavily(monkeypatch):
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    monkeypatch.setattr(
        tools,
        "search_web",
        lambda query_text, max_results=8, mode="auto": [
            {"title": "T", "link": "https://t.com", "snippet": "s", "provider": "tavily"}
        ],
    )

    results = tools.search_tool("acme stock")
    assert len(results) == 1
    assert results[0]["provider"] == "tavily"


def test_search_tool_falls_back_to_duckduckgo(monkeypatch):
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    monkeypatch.setattr(
        tools,
        "search_web",
        lambda query_text, max_results=8, mode="auto": [
            {"title": "D", "link": "https://d.com", "snippet": "s", "provider": "duckduckgo"}
        ],
    )

    results = tools.search_tool("acme stock")
    assert len(results) == 1
    assert results[0]["provider"] == "duckduckgo"


def test_search_tool_uses_google_when_configured(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "google")
    monkeypatch.setattr(
        tools,
        "search_web",
        lambda query_text, max_results=8, mode="auto": [
            {"title": "G", "link": "https://g.com", "snippet": "s", "provider": "google"}
        ],
    )

    results = tools.search_tool("acme stock")
    assert len(results) == 1
    assert results[0]["provider"] == "google"


def test_query_plan_is_enriched_with_primary_source_queries():
    plan = {
        "stock": "Microsoft",
        "pillars": [
            {
                "pillar_name": "Financial Engine",
                "objective": "test",
                "queries": [{"query": "Microsoft revenue growth", "intent": "generic"}],
            }
        ],
    }

    enriched = tools._enrich_query_plan_with_primary_source_queries(plan, "Microsoft")
    queries = [item["query"].lower() for item in enriched["pillars"][0]["queries"]]

    assert any("annual report" in query and "filetype:pdf" in query for query in queries)
    assert any("quarterly results" in query or "earnings presentation" in query for query in queries)


def test_scrape_site_uses_fast_path(monkeypatch):
    monkeypatch.setattr(tools, "_scrape_site_fast", lambda url: ("Title", "x" * 1200))
    monkeypatch.setattr(tools, "_scrape_site_selenium", lambda url: ("Browser", "y" * 2000))
    monkeypatch.setattr(tools, "SB", object())

    title, body = tools.scrape_site("https://example.com")
    assert title == "Title"
    assert len(body) == 1200


def test_scrape_site_falls_back_to_browser(monkeypatch):
    def fail_fast(url):
        raise RuntimeError("fail")

    monkeypatch.setattr(tools, "_scrape_site_fast", fail_fast)
    monkeypatch.setattr(tools, "_scrape_site_selenium", lambda url: ("Browser", "y" * 2000))
    monkeypatch.setattr(tools, "SB", object())

    title, body = tools.scrape_site("https://example.com")
    assert title == "Browser"
    assert len(body) == 2000


def test_scrape_site_skips_browser_when_disallowed(monkeypatch):
    monkeypatch.setattr(tools, "_scrape_site_fast", lambda url: ("Thin", "x" * 50))
    monkeypatch.setattr(tools, "_scrape_site_selenium", lambda url: ("Browser", "y" * 2000))
    monkeypatch.setattr(tools, "SB", object())

    details = tools.scrape_site_detailed("https://example.com", allow_browser_fallback=False, min_body_length=400)

    assert details["title"] == "Thin"
    assert details["scrape_method"] == "fast_http_thin"
    assert details["character_count"] == 50


def test_scrape_site_uses_cloudflare_before_selenium(monkeypatch):
    monkeypatch.setattr(tools, "_scrape_site_fast", lambda url: ("Thin", "x" * 50))
    monkeypatch.setattr(tools, "_cloudflare_browser_rendering_enabled", lambda: True)
    monkeypatch.setattr(tools, "_scrape_site_cloudflare", lambda url: ("Rendered", "y" * 1200, 321.0))
    monkeypatch.setattr(tools, "_scrape_site_selenium", lambda url: ("Browser", "z" * 2000))
    monkeypatch.setattr(tools, "SB", object())

    details = tools.scrape_site_detailed("https://example.com", allow_browser_fallback=True, min_body_length=400)

    assert details["title"] == "Rendered"
    assert details["scrape_method"] == "cloudflare_markdown"
    assert details["browser_ms_used"] == 321.0


def test_scrape_site_uses_pdf_fast_path(monkeypatch):
    monkeypatch.setattr(tools, "_scrape_site_fast", lambda url: ("Annual Report", "p" * 2500))

    details = tools.scrape_site_detailed("https://example.com/report.pdf")

    assert details["title"] == "Annual Report"
    assert details["scrape_method"] == "fast_http"
    assert details["source_kind"] == "pdf"
    assert details["content_type"] == "application/pdf"
    assert details["character_count"] == 2500
