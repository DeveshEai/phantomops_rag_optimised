"""
tools/web_search.py — Multi-Provider Web Search Tools

Two LangChain-compatible search tools the LLM agent picks between:
- serpapi_search  → Google results (businesses, local listings, general)
- tavily_search   → AI-optimized results (research, news, in-depth topics)

Both accept an optional `time_range` to bias toward fresh results
("day" | "week" | "month" | "year"). Leave empty for all-time relevance.
"""

import requests
from langchain_core.tools import tool
from core.config import SERPAPI_KEY, TAVILY_API_KEY

# Map a friendly time_range to Google's tbs (time-based search) value.
_SERP_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
# Tavily accepts these directly.
_TAVILY_RANGES = {"day", "week", "month", "year"}

# Output caps — keep tool returns small so the accumulated GATHER context and the
# research brief fed to the WRITE phase stay under the provider's per-minute token
# limit (Groq free tier ~12k TPM). Tavily "advanced" content can be 1-2k chars per
# result; uncapped, a few searches blow the budget.
_MAX_RESULT_CHARS = 700    # per individual result's snippet/content
_MAX_TOTAL_CHARS  = 3500   # per single search call (all results combined)


def _cap(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


@tool
def serpapi_search(query: str, time_range: str = "") -> str:
    """Search Google via SerpAPI. Use for finding businesses, doctors, local listings, or general web results.

    time_range: optional recency filter — "day", "week", "month", or "year".
    Pass it (e.g. "month") when the user wants recent/latest information.
    """
    try:
        from serpapi import GoogleSearch
        params = {"q": query, "api_key": SERPAPI_KEY, "num": 5}
        tbs = _SERP_TBS.get((time_range or "").lower().strip())
        if tbs:
            params["tbs"] = tbs
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        if not organic:
            return "No results found."
        blocks = [
            f"Title: {r.get('title')}\nURL: {r.get('link')}\n"
            f"Date: {r.get('date', 'n/a')}\nSnippet: {_cap(r.get('snippet', ''), _MAX_RESULT_CHARS)}"
            for r in organic[:5]
        ]
        return _cap("\n\n".join(blocks), _MAX_TOTAL_CHARS)
    except Exception as e:
        return f"SerpAPI search failed: {e}"


@tool
def tavily_search(query: str, time_range: str = "") -> str:
    """Search the web via Tavily. Use for research reports, latest news, and in-depth topic exploration.

    time_range: optional recency filter — "day", "week", "month", or "year".
    Pass it (e.g. "week" for news, "month" for trends) when the user wants the
    latest information. Leave empty for evergreen/background facts.
    """
    try:
        from tavily import TavilyClient
        kwargs = {"query": query, "max_results": 5, "search_depth": "advanced"}
        tr = (time_range or "").lower().strip()
        if tr in _TAVILY_RANGES:
            kwargs["time_range"] = tr
            kwargs["topic"] = "news"   # news topic honours recency best
        response = TavilyClient(api_key=TAVILY_API_KEY).search(**kwargs)
        results = response.get("results", [])
        if not results:
            return "No results found."
        blocks = [
            f"Title: {r.get('title')}\nURL: {r.get('url')}\n"
            f"Published: {r.get('published_date', 'n/a')}\nContent: {_cap(r.get('content', ''), _MAX_RESULT_CHARS)}"
            for r in results
        ]
        return _cap("\n\n".join(blocks), _MAX_TOTAL_CHARS)
    except Exception as e:
        return f"Tavily search failed: {e}"
