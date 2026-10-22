"""
rag/ingestion.py — document ingestion: where KB text comes from.

Three source paths feed rag.store.ingest_if_new: the intake folder
(scan_intake_folder, polled by run_agent.py), a web link (fetch_url_text,
used by agents/assistant_agent.py's "save this to RAG" flow), and email
attachments/body text (assistant_agent.py already has the extracted text in
hand and calls rag.store.ingest_if_new directly — no extraction helper
needed here for that path).
"""

import os
import re
from datetime import datetime, timezone

from rag.embedding import available
from rag.store import ingest_if_new

_ARABIC_CHAR_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿ]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str:
    """Cheap script-based language tag ('en'/'ar'/'mixed') for the rag_sources
    audit log — same heuristic as assistant_agent._detect_language_fallback,
    duplicated locally to avoid a reverse import (assistant_agent already
    imports from this package). Good enough for an audit column; not used
    for any routing decision."""
    ar = len(_ARABIC_CHAR_RE.findall(text or ""))
    en = len(_LATIN_CHAR_RE.findall(text or ""))
    if ar == 0:
        return "en"
    if en == 0:
        return "ar"
    minority_ratio = min(ar, en) / max(ar, en)
    return "mixed" if minority_ratio > 0.15 else ("ar" if ar > en else "en")


def fetch_url_text(url: str, timeout: int = 15) -> str:
    """Fetch `url` and return clean, boilerplate-stripped text (nav/ads/footers
    removed) for RAG ingestion. Returns "" on any failure (bad URL, timeout,
    no extractable content) — callers should treat that as "nothing to save",
    not raise, since this runs off an email/web-link "save to RAG" command
    that must still get a sensible reply either way."""
    try:
        import requests
        import trafilatura
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, url=url) or ""
        return text.strip()
    except Exception as e:
        print(f"[RAG] Failed to fetch web link {url}: {e}")
        return ""


def _extract_text_for_ingest(file_path: str) -> str:
    """Extract text from a file for RAG ingestion. Covers everything
    tools.file_reader's attachment dispatcher supports (pdf/pptx/docx/xlsx/csv),
    plus plain .txt — glossaries/style guides dropped as raw text aren't email
    attachments, so file_reader's dispatcher doesn't cover them. Returns ""
    for anything unsupported so the caller can skip it silently."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".txt":
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""
    from tools.file_reader import read_attachment_text, SUPPORTED_EXTENSIONS
    if ext not in SUPPORTED_EXTENSIONS:
        return ""
    return read_attachment_text(file_path)


def _file_metadata(file_path: str) -> dict:
    name = os.path.basename(file_path)
    meta = {
        "file_name": name,
        "title": os.path.splitext(name)[0],
    }
    try:
        meta["source_date"] = datetime.fromtimestamp(
            os.path.getmtime(file_path), timezone.utc
        ).date().isoformat()
    except Exception:
        pass
    return meta


def scan_intake_folder() -> int:
    """Scan core.config.INTAKE_DIR for files not yet in the RAG KB and ingest
    them. Runs alongside — not instead of — the existing on-demand "read this
    file and answer" behavior (agents/assistant_agent.py's _resolve_intake_file):
    auto-ingest just makes the same file permanently searchable too. Best-effort
    per file — one bad/unsupported file never blocks the rest of the scan or the
    caller's poll loop. Returns the number of files newly ingested."""
    from core.config import INTAKE_DIR
    if not os.path.isdir(INTAKE_DIR):
        return 0
    if not available():
        print("[RAG] Skipping intake auto-ingest — optional RAG dependency not installed "
              "(chromadb + langchain-ollama). Install them to enable.")
        return 0
    ingested = 0
    for name in os.listdir(INTAKE_DIR):
        path = os.path.join(INTAKE_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            text = _extract_text_for_ingest(path)
            if not text or not text.strip():
                continue
            n = ingest_if_new(text, source_type="intake_file", source_ref=name,
                              language=detect_language(text),
                              metadata=_file_metadata(path))
            if n:
                ingested += 1
                print(f"[RAG] Ingested intake file into KB: {name} ({n} chunks)")
        except Exception as e:
            print(f"[RAG] Failed to ingest intake file {name}: {e}")
    return ingested
