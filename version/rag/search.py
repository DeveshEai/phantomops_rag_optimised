"""
rag/search.py — hybrid (vector + lexical) search over the KB + relevance
floor + prompt formatting for the WRITE phase.

Combines dense/vector search (Chroma) with BM25 lexical search (rank_bm25),
fused via Reciprocal Rank Fusion (RRF). Dense embeddings capture semantic
similarity well but can under-rank exact term/keyword matches — a real gap
for the search_knowledge_base tool (tools/rag_search.py), which the model
uses to fire off short, precise lookup queries ("refund policy", "shipping
delivery time") rather than one long natural-language sentence. BM25 is the
standard complement for exactly that query shape. rank_bm25 is an optional,
lazily-imported dependency — retrieve() degrades to exactly the original
dense-only behavior (same results, same score scale) when it isn't installed.
"""

import re

from core.config import TOP_K_RETRIEVAL, RAG_MIN_SCORE
from rag.embedding import available, get_embedder
from rag.store import _get_collection, get_all_chunks

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_RRF_K = 60  # standard constant from the Reciprocal Rank Fusion paper

# Common stopwords, filtered before BM25 scoring. Without this, a stopword
# that happens to appear in only one document of a SMALL knowledge base gets
# an inflated IDF (rare-across-corpus reads as "important" to BM25's math,
# even though it's semantically empty) — confirmed as a real bug during
# testing: "are" appearing once ("Refunds are processed...") gave an
# off-topic greeting a false-positive match purely because "are" was also in
# the query. Larger corpora self-correct (stopwords appear almost everywhere,
# driving IDF toward zero); a KB this size can't rely on that. Small,
# hardcoded lists — same lightweight-tokenization philosophy as
# tools/asset_manager.py's _stem_words(), not a new NLP dependency.
_STOPWORDS_EN = {
    "a", "an", "the", "and", "or", "but", "if", "so", "of", "in", "on", "at",
    "to", "for", "with", "as", "by", "from", "is", "are", "was", "were", "be",
    "been", "being", "am", "it", "its", "this", "that", "these", "those", "i",
    "you", "he", "she", "we", "they", "them", "his", "her", "their", "our",
    "your", "my", "me", "us", "do", "does", "did", "not", "no", "yes", "can",
    "will", "would", "should", "could", "have", "has", "had", "what", "when",
    "where", "who", "why", "how", "there", "here", "just", "about", "into",
    "up", "down", "out", "off", "over", "under", "again", "then", "than",
    "too", "very", "s", "t", "don", "now",
}
_STOPWORDS_AR = {
    "في", "من", "إلى", "على", "عن", "مع", "هذا", "هذه", "ذلك", "تلك", "التي",
    "الذي", "هو", "هي", "أن", "إن", "كان", "كانت", "لا", "لم", "لن", "ما",
    "و", "أو", "ثم", "قد", "كل", "بعض", "غير", "بين", "عند", "له", "لها",
}
_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_AR


def _tokenize(text: str) -> list:
    """Lightweight word tokenizer for BM25 — lowercase + regex word-split +
    stopword removal, no heavy NLP dependency. Same philosophy as
    tools/asset_manager.py's _stem_words()."""
    return [w for w in _TOKEN_RE.findall((text or "").lower()) if w not in _STOPWORDS]


def _bm25_available() -> bool:
    try:
        import rank_bm25  # noqa: F401
        return True
    except ImportError:
        return False


def _rrf_fuse(ranked_id_lists: list, k: int = _RRF_K) -> dict:
    """Reciprocal Rank Fusion: combine multiple ranked id lists into one score
    per id (higher = more relevant). Fuses by RANK POSITION, not raw score
    magnitude — sidesteps the fact that cosine similarity and BM25 scores are
    on incomparable scales, with no per-query normalization needed. A single
    input list is order-preserving (still ranked the same way), just rescaled."""
    fused = {}
    for ranked_ids in ranked_id_lists:
        for rank, cid in enumerate(ranked_ids):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return fused


def _hit_from_meta(text: str, meta: dict, score: float) -> dict:
    meta = meta or {}
    return {
        "text": text,
        "source_type": meta.get("source_type"),
        "source_ref": meta.get("source_ref"),
        "title": meta.get("title"),
        "section_heading": meta.get("section_heading"),
        "file_name": meta.get("file_name"),
        "page_number": meta.get("page_number"),
        "page_end": meta.get("page_end"),
        "source_date": meta.get("source_date"),
        "language": meta.get("language"),
        "ingested_at": meta.get("ingested_at"),
        "score": score,
    }


def retrieve(query: str, top_k: int = TOP_K_RETRIEVAL, min_score: float = RAG_MIN_SCORE) -> list:
    """Return the relevant chunks for `query` (up to `top_k`), each as
    {"text", "source_type", "source_ref", "title", "section_heading",
    "file_name", "page_number", "source_date", "language", "ingested_at", "score"}
    (score: higher = more relevant — a raw cosine similarity when rank_bm25
    isn't installed, or a fused Reciprocal Rank Fusion score when hybrid
    search is active; either way, only the relative ordering is meaningful,
    not the absolute magnitude). "language"/"ingested_at" come straight from
    the chunk's own Chroma metadata (set by rag.store.ingest_if_new) — no
    separate SQLite lookup needed.

    Chunks scoring below `min_score` (core.config.RAG_MIN_SCORE) on the dense
    branch are dropped as not actually relevant — without this, an unrelated
    message like a bare "hi" still gets back the top-K of whatever's in the
    KB, and that irrelevant content then leaks into the reply. When hybrid
    search is active, a chunk can still surface via a genuine BM25 keyword
    match (score > 0, i.e. at least one query term actually appears in it)
    even if it didn't clear the dense floor — this is BM25 "rescuing" a
    precise short query dense under-ranked. Returns an empty list when
    nothing clears either bar.

    Empty list if the query is blank, the KB has nothing ingested yet, or the
    optional RAG dependencies aren't installed — see embedding.available()."""
    if not (query or "").strip():
        return []
    if not available():
        return []
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        return []

    fetch_k = min(top_k * 3, count)
    query_embedding = get_embedder().embed_query(query)
    result = collection.query(query_embeddings=[query_embedding], n_results=fetch_k)

    dense_ids = (result.get("ids") or [[]])[0]
    dense_docs = (result.get("documents") or [[]])[0]
    dense_metas = (result.get("metadatas") or [[]])[0]
    dense_dists = (result.get("distances") or [[]])[0]

    if not _bm25_available():
        # Exactly the original dense-only behavior — same results, same
        # cosine score scale, no functional change when the optional
        # rank_bm25 dependency isn't installed.
        hits = []
        for cid, doc, meta, dist in zip(dense_ids, dense_docs, dense_metas, dense_dists):
            score = (1.0 - dist) if dist is not None else None
            if score is not None and score < min_score:
                continue
            hits.append(_hit_from_meta(doc, meta, score))
        return hits[:top_k]

    # ── Hybrid: dense + BM25, fused by rank (RRF) ──────────────────────────
    chunk_info = {}
    dense_ranked_ids = []
    for cid, doc, meta, dist in zip(dense_ids, dense_docs, dense_metas, dense_dists):
        score = (1.0 - dist) if dist is not None else None
        if score is not None and score < min_score:
            continue
        dense_ranked_ids.append(cid)
        chunk_info[cid] = {"text": doc, "meta": meta or {}}

    bm25_ranked_ids = []
    corpus = get_all_chunks()
    if corpus["ids"]:
        from rank_bm25 import BM25Okapi
        tokenized_docs = [_tokenize(d) for d in corpus["documents"]]
        bm25 = BM25Okapi(tokenized_docs)
        scores = bm25.get_scores(_tokenize(query))
        scored = [(cid, s) for cid, s in zip(corpus["ids"], scores) if s > 0]
        scored.sort(key=lambda x: -x[1])
        bm25_ranked_ids = [cid for cid, _ in scored[:fetch_k]]
        for cid, doc, meta in zip(corpus["ids"], corpus["documents"], corpus["metadatas"]):
            if cid in bm25_ranked_ids and cid not in chunk_info:
                chunk_info[cid] = {"text": doc, "meta": meta or {}}

    if not dense_ranked_ids and not bm25_ranked_ids:
        return []

    fused_scores = _rrf_fuse([lst for lst in (dense_ranked_ids, bm25_ranked_ids) if lst])
    ranked = sorted(fused_scores.items(), key=lambda x: -x[1])[:top_k]

    hits = []
    for cid, score in ranked:
        info = chunk_info.get(cid)
        if not info:
            continue
        hits.append(_hit_from_meta(info["text"], info["meta"], score))
    return hits


def format_context(hits: list) -> str:
    """Render retrieve() hits as a labeled context block for the WRITE-phase
    prompt — same shape as the existing search_brief context from the
    web-search tool loop, so it slots into the prompt the same way. The date
    (just the day, not a timestamp) is included when known, so a WRITE call
    can judge freshness (e.g. two conflicting saved policies, prefer the
    newer one) without a separate database lookup."""
    if not hits:
        return ""
    parts = []
    for h in hits:
        label = (
            h.get("title")
            or h.get("file_name")
            or h.get("source_ref")
            or h.get("source_type")
            or "unknown source"
        )
        details = []
        if h.get("file_name") and h.get("file_name") != label:
            details.append(f"file {h.get('file_name')}")
        if h.get("section_heading"):
            details.append(f"section {h.get('section_heading')}")
        if h.get("page_number") is not None:
            if h.get("page_end") and h.get("page_end") != h.get("page_number"):
                details.append(f"pages {h.get('page_number')}-{h.get('page_end')}")
            else:
                details.append(f"page {h.get('page_number')}")
        if h.get("source_date"):
            details.append(f"source date {h.get('source_date')}")
        if h.get("language"):
            details.append(f"language {h.get('language')}")
        saved_on = (h.get("ingested_at") or "")[:10]  # YYYY-MM-DD, or "" if unknown
        if saved_on:
            details.append(f"saved {saved_on}")
        tag = f"[Source: {label}" + (f", {', '.join(details)}" if details else "") + "]"
        parts.append(f"{tag}\n{h.get('text', '')}")
    return "\n\n".join(parts)
