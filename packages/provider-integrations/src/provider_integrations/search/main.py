"""Search provider integration layer.

Each provider adapter returns normalized search result objects so downstream systems
can consume results from one stable contract regardless of source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from langchain_community.tools import DuckDuckGoSearchResults
import os
import requests

from research_core.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SearchItem:
    """Canonical search result record used across all providers."""

    title: str
    link: str
    snippet: str
    provider: str

    def as_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "link": self.link,
            "snippet": self.snippet,
            "provider": self.provider,
        }


class SearchProvider(ABC):
    """Adapter interface for search providers."""

    provider_name: str

    @abstractmethod
    def search(self, query_text: str, max_results: int = 8) -> list[dict[str, str]]:
        """Run a search and return canonical results."""


class ExaSearchProvider(SearchProvider):
    """Default provider using Exa search API."""

    provider_name = "exa"

    def search(self, query_text: str, max_results: int = 8) -> list[dict[str, str]]:
        api_key = os.getenv("EXA_API_KEY", "").strip() or os.getenv("EXA_API_TOKEN", "").strip()
        if not api_key:
            logger.debug("EXA_API_KEY missing; skipping Exa provider")
            return []

        payload = {
            "query": query_text,
            "numResults": max_results,
        }
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = requests.post(
                "https://api.exa.ai/search",
                json=payload,
                headers=headers,
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Exa search request failed for query='%s': %s", query_text, exc)
            return []

        payload = response.json()
        if not isinstance(payload, dict):
            return []

        results = payload.get("results") or payload.get("data") or []
        if not isinstance(results, list):
            return []

        normalized: list[dict[str, str]] = []
        for result in results[:max_results]:
            if not isinstance(result, dict):
                continue
            title = str(result.get("title", "")).strip()
            link = str(result.get("url", result.get("link", ""))).strip()
            snippet = str(result.get("text", result.get("snippet", "")).strip())
            if not link:
                continue
            normalized.append(SearchItem(title=title, link=link, snippet=snippet, provider=self.provider_name).as_dict())
        return normalized


class TavilySearchProvider(SearchProvider):
    """Tavily search adapter."""

    provider_name = "tavily"

    def search(self, query_text: str, max_results: int = 8) -> list[dict[str, str]]:
        api_key = os.getenv("TAVILY_API_KEY", "").strip() or os.getenv("TAVI_API_KEY", "").strip()
        if not api_key:
            return []

        payload = {
            "api_key": api_key,
            "query": query_text,
            "max_results": max_results,
            "topic": "news",
            "search_depth": "advanced",
            "include_answer": False,
            "include_raw_content": False,
        }

        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Tavily search failed for query='%s': %s", query_text, exc)
            return []

        payload_json = response.json()
        if not isinstance(payload_json, dict):
            return []

        normalized: list[dict[str, str]] = []
        for result in payload_json.get("results", [])[:max_results]:
            if not isinstance(result, dict):
                continue
            normalized.append(
                SearchItem(
                    title=str(result.get("title", "")),
                    link=str(result.get("url", "")),
                    snippet=str(result.get("content", "")),
                    provider=self.provider_name,
                ).as_dict()
            )
        return normalized


class GoogleSerpApiSearchProvider(SearchProvider):
    """Google SerpAPI adapter."""

    provider_name = "google"

    def search(self, query_text: str, max_results: int = 8) -> list[dict[str, str]]:
        api_key = os.getenv("SERPAPI_API_KEY", "").strip()
        if not api_key:
            return []

        try:
            response = requests.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google",
                    "q": query_text,
                    "api_key": api_key,
                    "num": max_results,
                },
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("SerpAPI search failed for query='%s': %s", query_text, exc)
            return []

        payload = response.json()
        if not isinstance(payload, dict):
            return []

        normalized: list[dict[str, str]] = []
        for result in payload.get("organic_results", [])[:max_results]:
            if not isinstance(result, dict):
                continue
            normalized.append(
                SearchItem(
                    title=str(result.get("title", "")),
                    link=str(result.get("link", "")),
                    snippet=str(result.get("snippet", "")),
                    provider=self.provider_name,
                ).as_dict()
            )
        return normalized


class DuckDuckGoSearchProvider(SearchProvider):
    """DuckDuckGo search adapter."""

    provider_name = "duckduckgo"

    def search(self, query_text: str, max_results: int = 8) -> list[dict[str, str]]:
        search = DuckDuckGoSearchResults(output_format="list")
        try:
            response = search.invoke(query_text)
        except Exception as exc:
            logger.warning("DuckDuckGo search failed for query='%s': %s", query_text, exc)
            return []

        normalized: list[dict[str, str]] = []
        for item in response[:max_results]:
            if not isinstance(item, dict):
                continue
            normalized.append(
                SearchItem(
                    title=str(item.get("title", "")),
                    link=str(item.get("href", item.get("link", ""))),
                    snippet=str(item.get("snippet", item.get("body", ""))),
                    provider=self.provider_name,
                ).as_dict()
            )
        return normalized


def _provider_chain(mode: str) -> list[SearchProvider]:
    """Build provider chain by mode with default Exa-first ordering."""
    all_providers: dict[str, SearchProvider] = {
        "exa": ExaSearchProvider(),
        "tavily": TavilySearchProvider(),
        "google": GoogleSerpApiSearchProvider(),
        "ddg": DuckDuckGoSearchProvider(),
    }

    mode = (mode or "auto").strip().lower()

    if mode in {"auto", "default", ""}:
        return [
            all_providers["exa"],
            all_providers["tavily"],
            all_providers["google"],
            all_providers["ddg"],
        ]

    if mode == "exa":
        return [all_providers["exa"], all_providers["ddg"]]
    if mode == "tavily":
        return [all_providers["tavily"], all_providers["ddg"]]
    if mode == "google":
        return [all_providers["google"], all_providers["ddg"]]
    if mode == "ddg":
        return [all_providers["ddg"]]

    return [all_providers["ddg"]]


def search_web(query_text: str, max_results: int = 8, mode: str = "auto") -> list[dict[str, Any]]:
    """Run search across provider chain and return first successful non-empty result."""
    query = (query_text or "").strip()
    if not query:
        logger.debug("Skipping empty query")
        return []

    for provider in _provider_chain(mode):
        result_items = provider.search(query, max_results=max_results)
        if result_items:
            logger.info("Search query completed via %s with %d results", provider.provider_name, len(result_items))
            return result_items
        logger.info("Search provider %s returned no results; trying fallback", provider.provider_name)

    logger.warning("Search returned no results for query='%s' across configured providers", query)
    return []
