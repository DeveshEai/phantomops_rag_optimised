"""
rag/store.py — ChromaDB persistence: collection lifecycle, content hashing,
and the chunk+embed+upsert write path.

Single persistent collection per deployment (core.config.CHROMA_PERSIST_DIR),
resolved relative to the delivery folder so every install starts with its
own empty KB.
"""

import hashlib
import os

from core.config import CHROMA_PERSIST_DIR, CHROMA_COLLECTION_NAME
from rag.chunking import CHUNKING_STRATEGY_VERSION, chunk_text_with_metadata
from rag.embedding import available, get_embedder

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection
    import chromadb
    _client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    # Cosine distance so search.retrieve()'s score conversion (1 - distance)
    # is a meaningful similarity measure regardless of embedding-vector magnitude.
    _collection = _client.get_or_create_collection(
        name=CHROMA_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    return _collection


def get_all_chunks() -> dict:
    """Return every currently-persisted chunk as {"ids", "documents", "metadatas"}
    (Chroma's own field names) — the raw corpus rag.search's BM25 branch needs
    to build a lexical index from. No new storage: this just reads back exactly
    what ingest() already wrote, so BM25 hits carry the same citation metadata
    as vector hits for free. Returns empty lists if RAG isn't available or the
    KB is empty."""
    if not available():
        return {"ids": [], "documents": [], "metadatas": []}
    collection = _get_collection()
    if collection.count() == 0:
        return {"ids": [], "documents": [], "metadatas": []}
    result = collection.get(include=["documents", "metadatas"])
    return {
        "ids": result.get("ids") or [],
        "documents": result.get("documents") or [],
        "metadatas": result.get("metadatas") or [],
    }


def content_hash(text: str) -> str:
    # Include the chunking strategy so existing unchanged sources re-embed once
    # when chunk boundaries improve, then dedupe normally on later scans.
    payload = f"{CHUNKING_STRATEGY_VERSION}\0{text or ''}"
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _source_defaults(source_type: str, source_ref: str, metadata: dict = None) -> dict:
    meta = {**(metadata or {})}
    ref = source_ref or ""

    if "file_name" not in meta and source_type in ("intake_file", "email"):
        base = os.path.basename(ref)
        if base and "." in base:
            meta["file_name"] = base

    if "title" not in meta:
        if meta.get("file_name"):
            meta["title"] = os.path.splitext(meta["file_name"])[0]
        elif source_type == "web":
            meta["title"] = ref
        elif source_type == "email":
            meta["title"] = "Email note"

    return meta


def _clean_metadata(metadata: dict) -> dict:
    cleaned = {}
    for key, value in (metadata or {}).items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        if isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned


def ingest(text: str, source_type: str, source_ref: str, metadata: dict = None) -> int:
    """Chunk + embed + upsert `text` into the KB under `source_ref`. Returns the
    number of chunks written (0 if `text` was empty). Re-ingesting the same
    source_ref replaces its prior chunks (stable per-source IDs + an explicit
    delete-before-upsert), so an edited/re-submitted source updates in place
    instead of accumulating duplicate or stale trailing chunks. No-ops (returns
    0) if the optional RAG dependencies aren't installed — see embedding.available()."""
    if not available():
        print("[RAG] Skipping ingest — optional RAG dependency not installed "
              "(chromadb + langchain-ollama). Install them to enable.")
        return 0
    base_metadata = _source_defaults(source_type, source_ref, metadata)
    chunk_records = chunk_text_with_metadata(text, title=base_metadata.get("title", ""))
    if not chunk_records:
        return 0

    collection = _get_collection()
    chunks = [r["text"] for r in chunk_records]
    embeddings = get_embedder().embed_documents(chunks)
    ids = [f"{source_type}:{source_ref}:{i}" for i in range(len(chunks))]
    metadatas = [
        _clean_metadata({
            **base_metadata,
            **(record.get("metadata") or {}),
            "source_type": source_type,
            "source_ref": source_ref,
            "chunk_index": i,
        })
        for i, record in enumerate(chunk_records)
    ]

    try:
        collection.delete(where={"source_ref": source_ref})
    except Exception:
        pass

    collection.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    return len(chunks)


def ingest_if_new(text: str, source_type: str, source_ref: str, language: str = None,
                   metadata: dict = None) -> int:
    """Like ingest(), but no-ops if this exact content was already ingested for
    this source (per the rag_sources dedup ledger in core.database.sqlite_db),
    and records the ingestion on success. This is the entry point ingestion
    callers (folder auto-ingest, email/web save-to-rag commands — see
    rag/ingestion.py) should use. Returns the chunk count written (0 if
    skipped as an unchanged duplicate, or empty content).

    `language` and an ingestion timestamp are folded into each chunk's Chroma
    metadata (not just the SQLite rag_sources audit row) so search.retrieve()
    can surface them directly — e.g. to tell the WRITE phase how fresh/what
    language a hit is without a second database lookup per result."""
    from datetime import datetime, timezone
    from core.database.sqlite_db import has_rag_source, record_rag_source
    h = content_hash(text)
    if has_rag_source(source_type, source_ref, h):
        return 0
    full_metadata = {**(metadata or {})}
    if language:
        full_metadata["language"] = language
    full_metadata["ingested_at"] = datetime.now(timezone.utc).isoformat()
    n = ingest(text, source_type, source_ref, full_metadata)
    if n:
        record_rag_source(source_type, source_ref, h, n, language)
    return n
