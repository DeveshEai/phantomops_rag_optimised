"""
version/engines/ — Central Engine Registry for PhantomOps
Exposes all specialized functional engines under a clean, unified interface:
  - presentation: Marp CLI, 10-bundle theme rotation, Pexels real photo injection
  - documents: Word (.docx) and PDF creation & conversion
  - spreadsheets: Excel (.xlsx) calculation engine & CSV generation
  - rag: Hybrid ChromaDB vector + BM25 keyword search engine
  - research: Tavily + SerpAPI web search & browsing ('open', 'find')
  - extraction: Multi-format document parser & Tesseract OCR engine
  - llm: Multi-provider model inference & backoff execution engine
"""

from . import presentation
from . import documents
from . import spreadsheets
from . import rag
from . import research
from . import extraction
from . import llm

__all__ = [
    "presentation",
    "documents",
    "spreadsheets",
    "rag",
    "research",
    "extraction",
    "llm",
]

