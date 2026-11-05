"""
core/content_types.py — Shared Content Contracts and Document Validation

Provides typed contracts for multi-agent deliverables, section formatting,
table structures, language/RTL handling, and security sanitization
(such as Excel formula injection prevention).
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class ContentTable:
    """Explicitly structured tabular data."""
    headers: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    caption: Optional[str] = None

    def validate(self) -> List[str]:
        errors = []
        if not self.headers and not self.rows:
            return ["Table is completely empty."]
        expected_cols = len(self.headers) if self.headers else (len(self.rows[0]) if self.rows else 0)
        for idx, row in enumerate(self.rows):
            if len(row) != expected_cols:
                errors.append(f"Row {idx+1} has {len(row)} columns, expected {expected_cols}.")
        return errors


@dataclass
class ContentSection:
    """Individual section of a document or report."""
    heading: str
    content: str = ""
    table: Optional[ContentTable] = None
    list_items: List[str] = field(default_factory=list)
    source_references: List[str] = field(default_factory=list)


@dataclass
class StructuredDocument:
    """Shared document deliverable model across agents and renderers."""
    title: str
    sections: List[ContentSection] = field(default_factory=list)
    summary: str = ""
    recipient: Optional[str] = None
    language: str = "en"
    is_rtl: bool = False
    format: str = "docx"  # "docx", "pdf", "xlsx", "pptx", "text"
    metadata: Dict[str, Any] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors = []
        if not self.title or not self.title.strip():
            errors.append("Document title is missing or empty.")
        if not self.sections and not self.summary:
            errors.append("Document contains neither body sections nor a summary.")
        for idx, sec in enumerate(self.sections):
            if not sec.heading and not sec.content and not sec.table and not sec.list_items:
                errors.append(f"Section {idx+1} has no heading, content, table, or list items.")
            if sec.table:
                tbl_errs = sec.table.validate()
                for err in tbl_errs:
                    errors.append(f"Section '{sec.heading}': {err}")
        return errors


# ── Security & Formula Injection Guard ────────────────────────────────────────

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_excel_value(val: Any) -> Any:
    """
    Sanitize values placed in Excel cells.
    - Preserves numeric types (int, float), booleans, None.
    - Prevents CSV/Excel formula injection for untrusted text beginning with =, +, -, @.
    - If a string is clearly a number or safe text, leaves it alone.
    - If a string begins with a formula trigger character, prepends a single quote '
      so Excel treats it strictly as text.
    """
    if val is None or isinstance(val, (bool, int, float)):
        return val

    s = str(val)
    if not s:
        return s

    # If it is a string representation of a valid float/int, parse it as a number
    # (avoiding formula escape on negative numbers like -42 or -3.14)
    try:
        if re.match(r"^-?\d+(\.\d+)?$", s.strip()):
            if "." in s:
                return float(s.strip())
            return int(s.strip())
    except (ValueError, TypeError):
        pass

    # Guard against Excel formula injection
    if s.startswith(_FORMULA_PREFIXES):
        return "'" + s

    return s


def is_arabic_text(text: str) -> bool:
    """Check if the provided text contains substantial Arabic characters."""
    if not text:
        return False
    # Arabic unicode block ranges \u0600-\u06FF, \u0750-\u077F, \u08A0-\u08FF
    arabic_chars = len(re.findall(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]", text))
    total_letters = len(re.findall(r"[\w]", text))
    if total_letters == 0:
        return False
    return (arabic_chars / total_letters) > 0.2
