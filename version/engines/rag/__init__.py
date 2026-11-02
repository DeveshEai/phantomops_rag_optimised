"""
version/engines/rag/ — Hybrid ChromaDB + BM25 RRF Knowledge Base Engine
Handles document chunking, Ollama/bge-m3 embeddings, vector storage, and hybrid keyword+vector search.
"""

from rag.search import retrieve, format_context
from rag.ingestion import scan_intake_folder, fetch_url_text, detect_language
from rag.store import ingest, ingest_if_new, get_all_chunks
from rag.chunking import chunk_text, chunk_text_with_metadata
from tools.rag_search import search_knowledge_base

__all__ = [
    "retrieve",
    "format_context",
    "scan_intake_folder",
    "fetch_url_text",
    "detect_language",
    "ingest",
    "ingest_if_new",
    "get_all_chunks",
    "chunk_text",
    "chunk_text_with_metadata",
    "search_knowledge_base",
]
