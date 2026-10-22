"""
rag/ — Local RAG knowledge base (ChromaDB + Ollama embeddings).

Split into one module per pipeline stage so each concern can be read/changed
in isolation:

    rag/chunking.py   — split raw text into overlapping windows
    rag/embedding.py  — local Ollama embedder + optional-dependency gate
    rag/store.py      — ChromaDB collection lifecycle + dedup-checked writes
    rag/ingestion.py  — where text comes from (files, web links, intake folder)
    rag/search.py     — similarity search + relevance floor + prompt formatting

Callers outside this package should import from the specific submodule they
need (e.g. `from rag.search import retrieve`) so it's clear which stage of
the pipeline is being used. The re-exports below exist only for convenience
and back-compat with the pre-split `tools.rag_store` API.
"""

from rag.embedding import available
from rag.chunking import chunk_text, chunk_text_with_metadata
from rag.store import content_hash, ingest, ingest_if_new
from rag.ingestion import detect_language, fetch_url_text, scan_intake_folder
from rag.search import retrieve, format_context

__all__ = [
    "available",
    "chunk_text",
    "chunk_text_with_metadata",
    "content_hash",
    "ingest",
    "ingest_if_new",
    "detect_language",
    "fetch_url_text",
    "scan_intake_folder",
    "retrieve",
    "format_context",
]
