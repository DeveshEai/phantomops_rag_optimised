"""
version/engines/research/ — Live Multi-Source Web Research & Browsing Engine
Integrates Tavily, SerpAPI, and OpenAI-style in-page browsing ('open', 'find_in_page')
with raw-content salvaging and fallback synthesis.
"""

from tools.web_search import (
    tavily_search,
    serpapi_search,
    open_url,
    open_file,
    find_in_page,
)

__all__ = [
    "tavily_search",
    "serpapi_search",
    "open_url",
    "open_file",
    "find_in_page",
]

