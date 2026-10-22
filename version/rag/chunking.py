"""
rag/chunking.py - structure-aware text chunking for embedding.

Pure text transformation, no I/O and no optional deps, so this module is
always importable regardless of whether chromadb/langchain-ollama are
installed.
"""

import re

from core.config import CHUNK_SIZE, CHUNK_OVERLAP


CHUNKING_STRATEGY_VERSION = "structure-aware-v2"

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[.)])?\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_RE = re.compile(r"^\s*(?:\|.*\||[^\n]+\t[^\n]+)\s*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;:\u061f\u061b])\s+")
_PAGE_MARKER_RE = re.compile(r"^---\s*Page\s+(\d+)(?:\s*\([^)]*\))?\s*---$")
_SLIDE_MARKER_RE = re.compile(r"^Slide\s+(\d+):\s*$")


def _clean_lines(text: str) -> list[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]

    cleaned = []
    blank_seen = False
    for ln in lines:
        if not ln:
            if not blank_seen:
                cleaned.append("")
            blank_seen = True
            continue
        cleaned.append(ln)
        blank_seen = False
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return cleaned


def _heading_info(line: str):
    """Return (level, title) for likely section headings, else None."""
    s = (line or "").strip()
    if not s or len(s) > 120 or _TABLE_RE.match(s):
        return None

    m = _MD_HEADING_RE.match(s)
    if m:
        return len(m.group(1)), m.group(2).strip(" :-")

    m = _NUM_HEADING_RE.match(s)
    if m:
        title = m.group(2).strip(" :-")
        words = title.split()
        title_like = title.istitle() or title.upper() == title or len(words) <= 4
        if len(words) <= 10 and title_like and not re.search(r"[.!?]$", title):
            return m.group(1).count(".") + 1, f"{m.group(1)} {title}"

    if _BULLET_RE.match(s):
        return None

    words = s.split()
    alpha = re.sub(r"[^A-Za-z]", "", s)
    if 1 <= len(words) <= 10 and alpha and s.upper() == s and len(s) <= 90:
        return 1, s.strip(" :-")

    # Common in extracted docs: a compact label before bullets or a paragraph.
    if s.endswith(":") and 1 <= len(words) <= 10:
        return 3, s.strip(" :-")

    return None


def _section_path(stack: list[tuple[int, str]]) -> str:
    return " > ".join(title for _, title in stack if title)


def _render_unit(context: str, body: str) -> str:
    body = (body or "").strip()
    if not context:
        return body
    return f"Section: {context}\n\n{body}"


def _split_plain_window(text: str, max_len: int, overlap: int) -> list[str]:
    """Fallback splitter for a single line/sentence longer than the chunk size."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    overlap = min(max(0, overlap), max(0, max_len // 3))
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_len, n)
        if end < n:
            boundary = text.rfind(" ", start + max_len // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _tail_overlap(text: str, overlap: int) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text or overlap <= 0 or len(text) <= overlap:
        return text
    start = max(0, len(text) - overlap)
    boundary = text.find(" ", start)
    if boundary != -1 and boundary < len(text) - 1:
        start = boundary + 1
    return text[start:].strip()


def _split_oversized_unit(context: str, body: str, chunk_size: int, overlap: int) -> list[str]:
    prefix = f"Section: {context}\n\n" if context else ""
    body_limit = max(80, chunk_size - len(prefix))

    if "\n" in body:
        pieces = [p.strip() for p in body.split("\n") if p.strip()]
    else:
        pieces = [p.strip() for p in _SENTENCE_SPLIT_RE.split(body) if p.strip()]
    if not pieces:
        return []

    chunks = []
    current = ""
    for piece in pieces:
        if len(piece) > body_limit:
            if current:
                chunks.append(prefix + current)
                current = ""
            for part in _split_plain_window(piece, body_limit, overlap):
                chunks.append(prefix + part)
            continue

        candidate = piece if not current else f"{current} {piece}"
        if len(candidate) <= body_limit:
            current = candidate
        else:
            if current:
                chunks.append(prefix + current)
            tail = _tail_overlap(current, min(overlap, body_limit // 3))
            candidate = f"{tail} {piece}".strip() if tail else piece
            current = candidate if len(candidate) <= body_limit else piece

    if current:
        chunks.append(prefix + current)

    return [c.strip() for c in chunks if c.strip()]


def _emit_unit(units: list[tuple[str, str, int | None]], context: str,
               page_number: int | None, lines: list[str]) -> None:
    if not lines:
        return
    sep = "\n" if any(_BULLET_RE.match(ln) or _TABLE_RE.match(ln) for ln in lines) else " "
    body = sep.join(ln.strip() for ln in lines if ln.strip())
    body = re.sub(r" {2,}", " ", body).strip()
    if body:
        units.append((context, body, page_number))


def _parse_units(lines: list[str]) -> list[tuple[str, str, int | None]]:
    units = []
    heading_stack: list[tuple[int, str]] = []
    current: list[str] = []
    page_number = None

    for line in lines:
        if not line:
            _emit_unit(units, _section_path(heading_stack), page_number, current)
            current = []
            continue

        page = _PAGE_MARKER_RE.match(line) or _SLIDE_MARKER_RE.match(line)
        if page:
            _emit_unit(units, _section_path(heading_stack), page_number, current)
            current = []
            page_number = int(page.group(1))
            continue

        heading = _heading_info(line)
        if heading:
            _emit_unit(units, _section_path(heading_stack), page_number, current)
            current = []
            level, title = heading
            heading_stack = [(lvl, txt) for lvl, txt in heading_stack if lvl < level]
            heading_stack.append((level, title))
            continue

        current.append(line)

    _emit_unit(units, _section_path(heading_stack), page_number, current)
    return units


def _infer_title(units: list[tuple[str, str, int | None]], fallback: str = "") -> str:
    for context, body, _ in units:
        if context:
            return context.split(" > ", 1)[0].strip()
        first_line = (body or "").split("\n", 1)[0].strip()
        if first_line and len(first_line) <= 120:
            return first_line
    return (fallback or "").strip()


def _metadata(title: str, section_heading: str, page_number: int | None,
              page_end: int | None = None) -> dict:
    meta = {}
    if title:
        meta["title"] = title
    if section_heading:
        meta["section_heading"] = section_heading
    if page_number is not None:
        meta["page_number"] = int(page_number)
    if page_end is not None and page_end != page_number:
        meta["page_end"] = int(page_end)
    return meta


def chunk_text_with_metadata(text: str, chunk_size: int = CHUNK_SIZE,
                             chunk_overlap: int = CHUNK_OVERLAP,
                             title: str = "") -> list[dict]:
    """Return [{"text", "metadata"}] chunks while preserving document structure.

    The chunker keeps likely headings as section context, groups paragraphs,
    bullet lists, and table-like rows as complete units, and only falls back to
    bounded overlapping windows when an individual unit is too large.
    """
    lines = _clean_lines(text)
    if not lines:
        return []

    chunk_size = max(120, int(chunk_size or CHUNK_SIZE))
    chunk_overlap = max(0, int(chunk_overlap or 0))

    units = _parse_units(lines)
    if not units:
        return []

    title = (title or _infer_title(units)).strip()
    chunks = []
    current_context = None
    current_bodies: list[str] = []
    current_pages: list[int] = []

    def flush_current():
        nonlocal current_context, current_bodies, current_pages
        if current_bodies:
            page_start = min(current_pages) if current_pages else None
            page_end = max(current_pages) if current_pages else None
            chunks.append({
                "text": _render_unit(current_context or "", "\n\n".join(current_bodies)),
                "metadata": _metadata(title, current_context or "", page_start, page_end),
            })
        current_context = None
        current_bodies = []
        current_pages = []

    for context, body, page_number in units:
        rendered = _render_unit(context, body)
        if len(rendered) > chunk_size:
            flush_current()
            for part in _split_oversized_unit(context, body, chunk_size, chunk_overlap):
                chunks.append({
                    "text": part,
                    "metadata": _metadata(title, context, page_number),
                })
            continue

        candidate_bodies = current_bodies + [body]
        candidate_context = context if current_context is None else current_context
        candidate = _render_unit(candidate_context or "", "\n\n".join(candidate_bodies))

        if current_bodies and (context != current_context or len(candidate) > chunk_size):
            flush_current()

        if not current_bodies:
            current_context = context
        current_bodies.append(body)
        if page_number is not None:
            current_pages.append(page_number)

    flush_current()
    return [c for c in chunks if c.get("text", "").strip()]


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> list:
    """Split text into embedding chunks while preserving document structure."""
    return [c["text"] for c in chunk_text_with_metadata(text, chunk_size, chunk_overlap)]
