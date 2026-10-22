"""
rag/embedding.py — local Ollama embedder + the optional-dependency gate every
other rag/ module checks before touching chromadb or langchain_ollama.

Embeddings always run locally via Ollama (EMBEDDING_MODEL), independent of
whichever provider is pinned for replies — KB content never needs to leave
the machine just to be embedded, and English/Arabic content share one
collection since bge-m3 is multilingual.

chromadb/langchain_ollama imports are lazy (inside functions) so a
deployment that never uses RAG is unaffected even before these optional
packages are installed — mirrors the existing lazy-import pattern in
agents/__init__.py's build_agent_llm().
"""

from core.config import EMBEDDING_MODEL, OLLAMA_BASE_URL


def available() -> bool:
    """True if the optional RAG dependencies (chromadb, langchain-ollama) are
    installed. Every public function across rag/ checks this first and no-ops
    with a log line instead of raising when it's False — RAG is an opt-in
    feature, so a deployment that hasn't installed/pulled these must keep
    working exactly as it did before this feature existed."""
    try:
        import chromadb  # noqa: F401
        import langchain_ollama  # noqa: F401
        return True
    except ImportError:
        return False


def get_embedder():
    """Return a fresh OllamaEmbeddings client for EMBEDDING_MODEL."""
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_BASE_URL)
