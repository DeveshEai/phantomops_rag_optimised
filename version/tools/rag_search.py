"""
tools/rag_search.py — LLM-callable wrapper around the RAG knowledge base.

A thin @tool wrapper around rag.search's retrieve()/format_context(), used
by the GATHER phase's tool loop (agents/assistant_agent.py) alongside
serpapi_search/tavily_search — same pattern as tools/web_search.py, so the
model can call it repeatedly with different queries when a request needs
several distinct saved-info lookups (something the static one-shot RAG
pre-fetch elsewhere in the pipeline can't do).

Deliberately lives in tools/, not rag/, so rag/ stays free of any
langchain_core dependency (see md/portability_assessment.md).
"""

from langchain_core.tools import tool
from rag.search import retrieve, format_context


@tool
def search_knowledge_base(query: str) -> str:
    """Search the saved knowledge base — internal reference material the user
    or team has previously saved (policies, glossaries, prior research,
    documents). Use this for facts that might be documented internally, as
    DISTINCT from serpapi_search/tavily_search which fetch external/current
    information from the web. Call it more than once with different queries
    if the request needs several separate pieces of saved information (e.g.
    one call for "refund policy", another for "shipping policy") — each call
    is a fresh, independent lookup.

    Returns matching saved content with source citations, or says nothing
    relevant was found.
    """
    hits = retrieve(query)
    context = format_context(hits)
    return context or "No relevant information found in the knowledge base for this query."
