""" The tools our agent has access to """
#external
import html
import json
import os
import re
import time
from io import BytesIO
from typing import Any
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv
import requests
from langchain_openai import ChatOpenAI

#internal
from provider_integrations.search.main import search_web
from scope_api.observability.metrics import parse_llm_usage, record_llm_call, record_search_call
from research_core.prompts.tool_prompts import QUERY_GENERATION_PROMPT, QUERY_REFINEMENT_PROMPT, LLM_JUDGE
from research_core.prompts.tool_prompts import BATCH_LLM_JUDGE
from research_core.schemas.tool_schema import (
    QueryGenerationResponse,
    EvaluationResponse,
    BatchEvaluationResponse,
)
from scope_api.observability.telemetry import observe_span
from research_core.utils.logger import get_logger

logger = get_logger(__name__)

load_dotenv()

try:
    from seleniumbase import SB
except Exception:  # pragma: no cover - import fallback for minimal test envs
    SB = None

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - import fallback for minimal test envs
    PdfReader = None


CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", os.getenv("CF_ACCOUNT_ID", "")).strip()
CLOUDFLARE_API_TOKEN = os.getenv(
    "CLOUDFLARE_BROWSER_RENDERING_API_TOKEN",
    os.getenv("CF_BROWSER_RENDERING_API_TOKEN", os.getenv("CLOUDFLARE_API_TOKEN", "")),
).strip()
CLOUDFLARE_MARKDOWN_CACHE_TTL = int(os.getenv("CLOUDFLARE_MARKDOWN_CACHE_TTL", "300"))


def _parse_model_json_response(response: Any) -> dict[str, Any]:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return json.loads(content)

    if isinstance(content, list):
        text_blocks = [item.get("text", "") for item in content if isinstance(item, dict)]
        if text_blocks:
            return json.loads("".join(text_blocks))

    if isinstance(content, dict):
        return content

    raise ValueError(f"Unsupported model response content type: {type(content)!r}")


def search_tool(query: str | dict) -> list[dict[str, Any]]:
    """Run web search through the integration layer.

    Provider selection is controlled by SEARCH_PROVIDER:
    - exa (default chain starts with Exa)
    - tavily
    - google
    - ddg
    - auto (alias for provider chain prioritization)
    """
    if isinstance(query, dict):
        query_text = query.get("query", "").strip()
    else:
        query_text = str(query).strip()

    if not query_text:
        logger.warning("Skipping empty search query payload: %s", query)
        return []

    logger.info("Running search query: %s", query_text)
    provider_mode = os.getenv("SEARCH_PROVIDER", "auto").strip().lower()

    with observe_span(
        "tool.search",
        attributes={
            "tool.name": "search",
            "search.mode": provider_mode or "auto",
            "search.query_length": len(query_text),
        },
    ):
        started = time.perf_counter()
        results = search_web(query_text=query_text, max_results=8, mode=provider_mode or "auto")
        duration_seconds = time.perf_counter() - started

    provider = results[0].get("provider") if results else "none"
    record_search_call(
        provider=provider,
        mode=provider_mode or "auto",
        success=bool(results),
        duration_seconds=duration_seconds,
        result_count=len(results),
    )
    if results:
        logger.info(
            "Search query completed with provider=%s, results=%d",
            results[0].get("provider"),
            len(results),
        )
        return results

    logger.warning("Search returned no results for query='%s'", query_text)
    return []


def generate_search_queries(stock_name: str) -> dict[str, Any]:
    logger.info("Generating queries via LLM for stock=%s", stock_name)
    model_name = "gpt-4o-mini"
    started = time.perf_counter()
    success = False
    query_plan: dict[str, Any] = {"stock_name": stock_name, "pillars": []}
    draft_plan: dict[str, Any] = {"stock_name": stock_name, "pillars": []}
    input_tokens = 0
    output_tokens = 0
    draft_response = None
    refined_response = None
    try:
        prompt = QUERY_GENERATION_PROMPT.replace("{stock_name}", stock_name)
        model = ChatOpenAI(model=model_name, temperature=0, response_format=QueryGenerationResponse)
        draft_response = model.invoke(prompt)
        draft_raw = _parse_model_json_response(draft_response)
        draft_plan = QueryGenerationResponse.model_validate(draft_raw).model_dump()
        draft_input_tokens, draft_output_tokens = parse_llm_usage(getattr(draft_response, "usage_metadata", None))
        input_tokens += draft_input_tokens
        output_tokens += draft_output_tokens
        success = True

        review_prompt = (
            QUERY_REFINEMENT_PROMPT.replace("{stock_name}", stock_name).replace(
                "{query_plan_json}",
                json.dumps(draft_plan, indent=2),
            )
        )
        with observe_span("tool.generate_search_queries.refine"):
            refined_response = model.invoke(review_prompt)
            refined_raw = _parse_model_json_response(refined_response)
        query_plan = QueryGenerationResponse.model_validate(refined_raw).model_dump()
        success = True
    except Exception:
        logger.exception("Query refinement failed; using draft plan")
        if draft_response is not None:
            query_plan = draft_plan
        else:
            logger.warning("No valid draft query plan was produced; using empty fallback plan")

    if refined_response is not None:
        refined_input_tokens, refined_output_tokens = parse_llm_usage(
            getattr(refined_response, "usage_metadata", None)
        )
        input_tokens += refined_input_tokens
        output_tokens += refined_output_tokens
    duration_seconds = time.perf_counter() - started

    record_llm_call(
        operation="generate_search_queries",
        provider="openai",
        model=model_name,
        success=success,
        duration_seconds=duration_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    query_plan = _enrich_query_plan_with_primary_source_queries(query_plan, stock_name)
    logger.info("Query generation completed")
    return query_plan


def _stock_domain_hint(stock_name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", stock_name.lower())
    tokens = [
        token
        for token in cleaned.split()
        if len(token) >= 4 and token not in {"plc", "ltd", "bank", "group", "holdings", "corporation", "company"}
    ]
    return max(tokens, key=len) if tokens else ""


def _dedupe_queries(queries: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in queries:
        query_text = item.get("query", "").strip()
        if not query_text:
            continue
        key = query_text.lower()
        if key in seen:
            continue
        deduped.append({"query": query_text, "intent": item.get("intent", "").strip()})
        seen.add(key)
    return deduped


def _build_primary_source_seed_queries(stock_name: str, pillar_name: str) -> list[dict[str, str]]:
    domain_hint = _stock_domain_hint(stock_name)
    site_hint = f"site:{domain_hint}.com" if domain_hint else ""
    base_templates = {
        "Macro & Industry": [
            {
                "query": f"\"{stock_name}\" industry outlook 2025 2026 market growth filetype:pdf",
                "intent": "Find industry and market-growth materials tied to the company and its end markets.",
            },
            {
                "query": f"{site_hint} \"{stock_name}\" annual report strategy market demand filetype:pdf".strip(),
                "intent": "Find primary-source discussion of demand, markets, and strategic industry positioning in company reports.",
            },
        ],
        "Economic Moat": [
            {
                "query": f"{site_hint} \"{stock_name}\" investor relations annual report customers competitive advantage filetype:pdf".strip(),
                "intent": "Find management discussion of moat drivers such as customers, switching costs, and market position.",
            },
            {
                "query": f"\"{stock_name}\" market share competition brand contracts filetype:pdf",
                "intent": "Find evidence on competitive position, customer stickiness, and market-share durability.",
            },
        ],
        "Financial Engine": [
            {
                "query": f"{site_hint} \"{stock_name}\" annual report filetype:pdf revenue operating income cash flow".strip(),
                "intent": "Retrieve annual-report PDFs with revenue, margins, and cash-flow data from primary sources.",
            },
            {
                "query": f"{site_hint} \"{stock_name}\" quarterly results earnings presentation filetype:pdf".strip(),
                "intent": "Retrieve quarterly or earnings-presentation PDFs with recent financial metrics.",
            },
            {
                "query": f"\"{stock_name}\" 10-K OR annual report filetype:pdf revenue eps free cash flow",
                "intent": "Retrieve filing-style financial disclosures with core operating metrics.",
            },
        ],
        "Management & Capital Allocation": [
            {
                "query": f"{site_hint} \"{stock_name}\" proxy statement board governance filetype:pdf".strip(),
                "intent": "Find governance and board disclosures from primary-source filings or investor pages.",
            },
            {
                "query": f"{site_hint} \"{stock_name}\" capital allocation dividend buyback investor relations filetype:pdf".strip(),
                "intent": "Find management commentary on capital allocation, dividends, and buybacks.",
            },
        ],
        "Valuation": [
            {
                "query": f"{site_hint} \"{stock_name}\" investor relations quarterly results filetype:pdf guidance".strip(),
                "intent": "Retrieve primary-source guidance and earnings materials used to support valuation work.",
            },
            {
                "query": f"\"{stock_name}\" investor presentation filetype:pdf guidance outlook valuation",
                "intent": "Find investor presentations and outlook materials with valuation-relevant inputs.",
            },
        ],
        "Technical Analysis": [
            {
                "query": f"\"{stock_name}\" price volume moving average RSI last 90 days",
                "intent": "Retrieve recent price and volume coverage for short-window technical analysis.",
            },
            {
                "query": f"\"{stock_name}\" relative strength breakout volume 2026",
                "intent": "Find recent technical commentary and momentum evidence.",
            },
        ],
    }
    return [item for item in base_templates.get(pillar_name, []) if item["query"]]


def _enrich_query_plan_with_primary_source_queries(query_plan: dict[str, Any], stock_name: str) -> dict[str, Any]:
    max_queries_per_pillar = 12
    for pillar in query_plan.get("pillars", []):
        existing = [
            {"query": item.get("query", ""), "intent": item.get("intent", "")}
            for item in pillar.get("queries", [])
        ]
        seeded = _build_primary_source_seed_queries(stock_name, pillar.get("pillar_name", ""))
        merged = _dedupe_queries(seeded + existing)
        pillar["queries"] = merged[:max_queries_per_pillar]
    return query_plan


def _extract_text_from_html(raw_html: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    title = html.unescape(title_match.group(1).strip()) if title_match else ""
    without_scripts = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", without_scripts)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return title, text


def _derive_title_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    filename = path.rsplit("/", 1)[-1]
    if not filename:
        return ""
    cleaned = re.sub(r"\.(pdf|html?)$", "", filename, flags=re.IGNORECASE)
    cleaned = re.sub(r"[-_]+", " ", cleaned).strip()
    return cleaned[:180]


def _looks_like_pdf(url: str, content_type: str = "") -> bool:
    lowered_url = url.lower()
    lowered_content_type = content_type.lower()
    return lowered_url.endswith(".pdf") or "application/pdf" in lowered_content_type


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    if PdfReader is None or not pdf_bytes:
        return ""

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception:
        logger.exception("Failed to open PDF bytes")
        return ""

    chunks: list[str] = []
    for index, page in enumerate(reader.pages[:30]):
        try:
            page_text = (page.extract_text() or "").strip()
        except Exception:
            logger.warning("Failed to extract PDF page=%d", index)
            page_text = ""
        if page_text:
            chunks.append(page_text)
        if sum(len(chunk) for chunk in chunks) >= 40000:
            break
    return "\n".join(chunks)[:40000]


def _scrape_pdf_fast(url: str, response: requests.Response | None = None) -> tuple[str, str]:
    pdf_response = response or requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
    )
    pdf_response.raise_for_status()
    title = _derive_title_from_url(url)
    body = _extract_pdf_text(pdf_response.content)
    return title, body


def _scrape_site_fast(url: str) -> tuple[str, str]:
    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()
    if _looks_like_pdf(url, response.headers.get("Content-Type", "")):
        return _scrape_pdf_fast(url, response=response)
    return _extract_text_from_html(response.text)


def _scrape_site_selenium(url: str) -> tuple[str, str]:
    if SB is None:
        return "", ""
    with SB(uc=True, test=True) as sb:
        sb.uc_open_with_reconnect(url, reconnect_time=6)
        sb.sleep(2)
        title = sb.get_title() or ""
        body = sb.get_text("body") or ""
        return title, body


def _cloudflare_browser_rendering_enabled() -> bool:
    return bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN)


def _markdown_title(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:180]
    return ""


def _scrape_site_cloudflare(url: str) -> tuple[str, str, float]:
    if not _cloudflare_browser_rendering_enabled():
        raise RuntimeError("Cloudflare Browser Rendering credentials not configured")

    endpoint = (
        f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}"
        f"/browser-rendering/markdown?cacheTTL={CLOUDFLARE_MARKDOWN_CACHE_TTL}"
    )
    response = requests.post(
        endpoint,
        timeout=45,
        headers={
            "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"url": url},
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success") or not payload.get("result"):
        raise RuntimeError(f"Cloudflare Browser Rendering returned no markdown for {url}")

    browser_ms = float(response.headers.get("X-Browser-Ms-Used", 0) or 0.0)
    markdown = str(payload["result"]).strip()
    title = _markdown_title(markdown) or _derive_title_from_url(url)
    return title, markdown[:40000], browser_ms


def scrape_site_detailed(url: str, allow_browser_fallback: bool = True, min_body_length: int = 400) -> dict[str, Any]:
    logger.info("Scraping url=%s", url)
    started = time.perf_counter()
    source_kind = "pdf" if _looks_like_pdf(url) else "html"
    content_type = "application/pdf" if source_kind == "pdf" else "text/html"

    try:
        title, body = _scrape_site_fast(url)
        duration_seconds = time.perf_counter() - started
        if body and len(body) >= min_body_length:
            if _looks_like_pdf(url):
                content_type = "application/pdf"
            logger.info("Fast scrape completed for url=%s", url)
            return {
                "title": title,
                "body": body[:40000],
                "scrape_method": "fast_http",
                "source_kind": "pdf" if _looks_like_pdf(url) else "html",
                "content_type": content_type,
                "scrape_duration_seconds": round(duration_seconds, 3),
                "character_count": min(len(body), 40000),
            }
        if not allow_browser_fallback or _looks_like_pdf(url):
            logger.info("Fast scrape returned thin content; skipping browser fallback for url=%s", url)
            return {
                "title": title,
                "body": body[:40000],
                "scrape_method": "fast_http_thin",
                "source_kind": "pdf" if _looks_like_pdf(url) else "html",
                "content_type": content_type,
                "scrape_duration_seconds": round(duration_seconds, 3),
                "character_count": min(len(body), 40000),
            }
        logger.info("Fast scrape returned thin content; falling back to browser for url=%s", url)
    except Exception:
        if not allow_browser_fallback or _looks_like_pdf(url):
            duration_seconds = time.perf_counter() - started
            logger.warning("Fast scrape failed for url=%s; skipping browser fallback", url)
            return {
                "title": "",
                "body": "",
                "scrape_method": "fast_http_failed",
                "source_kind": source_kind,
                "content_type": content_type,
                "scrape_duration_seconds": round(duration_seconds, 3),
                "character_count": 0,
            }
        logger.warning("Fast scrape failed for url=%s; falling back to browser", url)

    if _cloudflare_browser_rendering_enabled():
        try:
            title, body, browser_ms_used = _scrape_site_cloudflare(url)
            duration_seconds = time.perf_counter() - started
            if body and len(body) >= min_body_length:
                logger.info("Cloudflare browser rendering completed for url=%s", url)
                return {
                    "title": title,
                    "body": body[:40000],
                    "scrape_method": "cloudflare_markdown",
                    "source_kind": source_kind,
                    "content_type": "text/markdown",
                    "scrape_duration_seconds": round(duration_seconds, 3),
                    "character_count": min(len(body), 40000),
                    "browser_ms_used": round(browser_ms_used, 3),
                }
            logger.info("Cloudflare browser rendering returned thin content for url=%s", url)
        except Exception:
            logger.warning("Cloudflare browser rendering failed for url=%s; falling back to Selenium", url)

    if SB is None:
        duration_seconds = time.perf_counter() - started
        logger.warning("Selenium fallback unavailable; returning empty scrape for url=%s", url)
        return {
            "title": "",
            "body": "",
            "scrape_method": "unavailable",
            "source_kind": source_kind,
            "content_type": content_type,
            "scrape_duration_seconds": round(duration_seconds, 3),
            "character_count": 0,
            "browser_ms_used": 0.0,
        }

    try:
        title, body = _scrape_site_selenium(url)
        duration_seconds = time.perf_counter() - started
        logger.info("Browser scrape completed for url=%s", url)
        return {
            "title": title,
            "body": body[:20000],
            "scrape_method": "browser_fallback",
            "source_kind": source_kind,
            "content_type": content_type,
            "scrape_duration_seconds": round(duration_seconds, 3),
            "character_count": min(len(body), 20000),
            "browser_ms_used": 0.0,
        }
    except Exception:
        duration_seconds = time.perf_counter() - started
        logger.exception("Browser scrape failed for url=%s", url)
        return {
            "title": "",
            "body": "",
            "scrape_method": "failed",
            "source_kind": source_kind,
            "content_type": content_type,
            "scrape_duration_seconds": round(duration_seconds, 3),
            "character_count": 0,
            "browser_ms_used": 0.0,
        }


def scrape_site(url: str) -> tuple[str, str]:
    details = scrape_site_detailed(url)
    return details["title"], details["body"]


def llm_as_a_judge(pillar: str, stock_name: str, title: str, excerpt: str) -> dict[str, Any]:
    logger.info("Running relevance judge for pillar=%s", pillar)
    model_name = "gpt-4o-mini"
    started = time.perf_counter()
    prompt = (
        LLM_JUDGE.replace("{pillar}", pillar)
        .replace("{stock_name}", stock_name)
        .replace("{title}", title)
        .replace("{excerpt}", excerpt)
    )
    with observe_span(
        "tool.llm_as_a_judge",
        attributes={
            "tool.name": "llm_as_a_judge",
            "llm.operation": "judge",
            "llm.model": model_name,
            "judge.pillar": pillar,
        },
    ):
        model = ChatOpenAI(model=model_name, temperature=0, response_format=EvaluationResponse)
        response = model.invoke(prompt)

    result_raw = _parse_model_json_response(response)
    result = EvaluationResponse.model_validate(result_raw).model_dump()
    usage = getattr(response, "usage_metadata", None)
    input_tokens, output_tokens = parse_llm_usage(usage)
    duration_seconds = time.perf_counter() - started
    record_llm_call(
        operation="judge",
        provider="openai",
        model=model_name,
        success=bool(result),
        duration_seconds=duration_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    logger.info("Judge completed for pillar=%s", pillar)
    return result


def llm_as_a_batch_judge(
    pillar: str,
    stock_name: str,
    candidates: list[dict[str, str]],
) -> list[dict[str, Any]]:
    logger.info("Running batch relevance judge for pillar=%s candidates=%d", pillar, len(candidates))
    if not candidates:
        return []

    model_name = "gpt-4o-mini"
    started = time.perf_counter()
    candidate_text = "\n\n".join(
        f"[{item['candidate_id']}]\nTitle: {item['title']}\nURL: {item['link']}\nSnippet: {item['snippet']}"
        for item in candidates
    )
    prompt = (
        BATCH_LLM_JUDGE.replace("{pillar}", pillar)
        .replace("{stock_name}", stock_name)
        .replace("{candidates}", candidate_text)
    )

    with observe_span(
        "tool.llm_as_a_batch_judge",
        attributes={
            "tool.name": "llm_as_a_batch_judge",
            "llm.operation": "judge_batch",
            "llm.model": model_name,
            "judge.pillar": pillar,
            "judge.batch_size": len(candidates),
        },
    ):
        model = ChatOpenAI(model=model_name, temperature=0, response_format=BatchEvaluationResponse)
        response = model.invoke(prompt)

    result_raw = _parse_model_json_response(response)
    result = BatchEvaluationResponse.model_validate(result_raw).model_dump()
    usage = getattr(response, "usage_metadata", None)
    input_tokens, output_tokens = parse_llm_usage(usage)
    duration_seconds = time.perf_counter() - started
    record_llm_call(
        operation="judge_batch",
        provider="openai",
        model=model_name,
        success=bool(result),
        duration_seconds=duration_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    logger.info("Batch judge completed for pillar=%s", pillar)
    return result["evaluations"]
