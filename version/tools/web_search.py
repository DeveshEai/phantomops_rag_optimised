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


from typing import Any

# Global cache of URLs from the most recent search call, so open(id=0) resolves
# correctly when models pass integer result indices instead of full URL strings.
_LAST_SEARCH_URLS: list[str] = []
# Global cache of the last opened webpage body for in-page search (find tool).
_LAST_PAGE_TEXT: str = ""



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
        _LAST_SEARCH_URLS.clear()
        blocks = []
        for r in organic[:5]:
            link = r.get("link", "")
            if link:
                _LAST_SEARCH_URLS.append(link)
            blocks.append(
                f"Title: {r.get('title')}\nURL: {link}\n"
                f"Date: {r.get('date', 'n/a')}\nSnippet: {_cap(r.get('snippet', ''), _MAX_RESULT_CHARS)}"
            )
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
        _LAST_SEARCH_URLS.clear()
        blocks = []
        for r in results:
            link = r.get("url", "")
            if link:
                _LAST_SEARCH_URLS.append(link)
            blocks.append(
                f"Title: {r.get('title')}\nURL: {link}\n"
                f"Published: {r.get('published_date', 'n/a')}\nContent: {_cap(r.get('content', ''), _MAX_RESULT_CHARS)}"
            )
        return _cap("\n\n".join(blocks), _MAX_TOTAL_CHARS)
    except Exception as e:
        return f"Tavily search failed: {e}"


def _html_to_text(html: str) -> str:
    import re, html as html_lib
    html = re.sub(r"<(script|style|nav|header|footer)[^>]*>[\s\S]*?</\1>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


@tool("open")
def open_url(id: Any = None, url: str = "", cursor: int = 0) -> str:
    """Open and inspect the webpage content of a URL or search result index.
    Accepts id (URL string or result index number like 0, 1) and optional cursor.
    """
    target = ""
    # Case 1: id is an integer (e.g. id=0 means first search result)
    if isinstance(id, int):
        if 0 <= id < len(_LAST_SEARCH_URLS):
            target = _LAST_SEARCH_URLS[id]
        elif _LAST_SEARCH_URLS:
            target = _LAST_SEARCH_URLS[0]
    elif isinstance(id, str):
        s_id = id.strip()
        if s_id.isdigit():
            idx = int(s_id)
            if 0 <= idx < len(_LAST_SEARCH_URLS):
                target = _LAST_SEARCH_URLS[idx]
        elif s_id.startswith("http://") or s_id.startswith("https://"):
            target = s_id

    if not target and url:
        target = str(url).strip()

    if not target:
        if _LAST_SEARCH_URLS:
            target = _LAST_SEARCH_URLS[0]
        else:
            return "No URL or search result found to open."

    if not target.startswith("http://") and not target.startswith("https://"):
        return f"Cannot open non-web URL: {target}"

    try:
        resp = requests.get(
            target,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            timeout=8,
        )
        if resp.status_code != 200:
            return f"Webpage returned status {resp.status_code} for {target}"
        text = _html_to_text(resp.text)
        global _LAST_PAGE_TEXT
        _LAST_PAGE_TEXT = text
        start_char = max(0, int(cursor)) if cursor else 0
        if start_char < len(text):
            text = text[start_char:]
        return _cap(text, _MAX_RESULT_CHARS * 4)
    except Exception as e:
        return f"Could not read webpage content ({target}): {e}"


@tool("open_file")
def open_file(path: Any = "", cursor: int = 0) -> str:
    """Read document content or check file reference."""
    import os
    s_path = str(path or "").strip()
    if not s_path or not os.path.isfile(s_path):
        return f"File reference: {s_path or 'none'}"
    try:
        with open(s_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            start = max(0, int(cursor)) if cursor else 0
            if start < len(content):
                content = content[start:]
            return _cap(content, _MAX_RESULT_CHARS * 4)
    except Exception as e:
        return f"Could not read file {s_path}: {e}"


@tool("find")
def find_in_page(pattern: Any = "", cursor: int = 0) -> str:
    """Find text matching pattern within the currently opened webpage (Ctrl+F).
    Accepts pattern (search term) and optional cursor.
    """
    global _LAST_PAGE_TEXT
    if not _LAST_PAGE_TEXT:
        return "No open webpage available to search."
    p = str(pattern or "").strip().lower()
    if not p:
        return _cap(_LAST_PAGE_TEXT, _MAX_RESULT_CHARS * 3)

    matches = []
    text_lower = _LAST_PAGE_TEXT.lower()
    start_pos = max(0, int(cursor)) if cursor else 0
    while True:
        idx = text_lower.find(p, start_pos)
        if idx == -1 or len(matches) >= 5:
            break
        s_start = max(0, idx - 80)
        s_end = min(len(_LAST_PAGE_TEXT), idx + len(p) + 120)
        snippet = _LAST_PAGE_TEXT[s_start:s_end].strip()
        matches.append(f"... {snippet} ...")
        start_pos = idx + len(p) + 10

    if matches:
        return _cap(f"Found matches for '{pattern}':\n\n" + "\n\n".join(matches), _MAX_RESULT_CHARS * 4)
    return f"Pattern '{pattern}' not found in the current page."



