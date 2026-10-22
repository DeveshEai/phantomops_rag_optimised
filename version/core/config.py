"""
config.py — Central Configuration

All settings live here. Change the MODEL names, paths, and parameters from
this single file without touching any other code.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Anchor all paths to the root directory (one level up from this file)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─────────────────────────────────────────────
# SEARCH API KEYS
# ─────────────────────────────────────────────
SERPAPI_KEY    = os.getenv("SERPAPI_KEY",    "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

def _find_shared_dir(name: str) -> str:
    """Same convention as the shared .venv in install.bat/start.bat: prefer an
    EXISTING `name` folder in the IMMEDIATE PARENT (shared across version_N
    delivery folders sitting beside each other — <parent>\\.venv, <parent>\\
    version_4, <parent>\\version_5 all reuse <parent>\\.venv) before creating a
    new one there. Used for OUTPUT_DIR and DATABASE_DIR so a new release
    connects to the same accumulated data (generated files, SQLite history,
    the RAG vector store) instead of starting over empty just because the code
    moved to a new folder. A brand-new machine with no prior install still
    gets its own independent, empty folder — it just lives one level up
    rather than inside a specific version's own folder.

    Deliberately only ONE level up, not several: a generic name like "output"
    or "database" can coincidentally already exist further up an arbitrary
    folder tree (verified this for real — a stale, unrelated C:\\output from
    a completely different old deployment was found and would have been
    silently reused when this searched 3 levels up, mixing new production
    data into old, disconnected data). The immediate parent is the only level
    that actually matches the intended "sibling version folders" convention;
    anything further up is someone else's folder, not necessarily ours."""
    return os.path.join(os.path.dirname(_BASE_DIR), name)


# ─────────────────────────────────────────────
# OUTPUT DIRECTORIES
# ─────────────────────────────────────────────
# OUTPUT_DIR honours the env var (so local/host runs can point it anywhere),
# otherwise it's auto-discovered/created next to the shared .venv (same
# convention as DATABASE_DIR below), one level up from this delivery folder —
# so downloaded attachments, generated files, and drafts also survive a
# version upgrade instead of being left behind in the old delivery folder. In
# Docker the host path is bind-mounted to /app/output and docker-compose sets
# OUTPUT_DIR=/app/output in the container env, so this resolves to the mount
# target there. Subdirs derive from it.
OUTPUT_DIR            = os.getenv("OUTPUT_DIR") or _find_shared_dir("output")
PPT_OUTPUT_DIR        = os.path.join(OUTPUT_DIR, "ppts")
PROPOSALS_OUTPUT_DIR  = os.path.join(OUTPUT_DIR, "proposals")
INBOX_ATTACHMENTS_DIR = os.path.join(OUTPUT_DIR, "inbox")

# ─────────────────────────────────────────────
# DATABASE DIRECTORY (SQLite + the Chroma vector store)
# ─────────────────────────────────────────────
# Both the SQLite DB (sally.db/phantomops.db) and the RAG vector store live
# under ONE shared location, DATABASE_DIR, instead of two different schemes
# (previously: SQLite under OUTPUT_DIR, Chroma in its own separately-searched
# parent folder) — one setting, one place to look, and both survive a version
# upgrade together. Override DATABASE_DIR in .env for an explicit location
# (e.g. a shared network drive); otherwise it's auto-discovered/created next
# to the shared .venv, one level up from this delivery folder.
DATABASE_DIR = os.getenv("DATABASE_DIR") or _find_shared_dir("database")

# ─────────────────────────────────────────────
# FILE DELIVERY / INTAKE (preconfigured folders)
# When asked, Sally writes deliverables to DELIVERY_DIR instead of emailing them,
# and reads requested input files from INTAKE_DIR.
#   DELIVERY_DIR → defaults to OUTPUT_DIR itself (the configured/mounted output path,
#                  e.g. C:/PhantomOpsV1.0/outputs) — that IS the storage location.
#   INTAKE_DIR   → defaults to an "intake" subfolder of OUTPUT_DIR (manager drop point).
# Override either in .env to point at a different network/shared path (mount it
# into the container if it lives outside OUTPUT_DIR).
# ─────────────────────────────────────────────
DELIVERY_DIR = os.getenv("DELIVERY_DIR", OUTPUT_DIR)
INTAKE_DIR   = os.getenv("INTAKE_DIR",   os.path.join(OUTPUT_DIR, "intake"))

# ─────────────────────────────────────────────
# COMPANY ASSETS  (C:\sally\Rag-Based-Bot\assets\)
# ─────────────────────────────────────────────
ASSETS_DIR = os.getenv("ASSETS_DIR") or os.path.join(_BASE_DIR, "assets")

# Asset subdirectories — add new templates/letterheads/logos/images by dropping
# files into these folders; the asset_manager will auto-index them on next run.
ASSETS_TEMPLATES_DIR   = os.path.join(ASSETS_DIR, "templates")
ASSETS_LETTERHEADS_DIR = os.path.join(ASSETS_DIR, "letterhead")
ASSETS_LOGOS_DIR       = os.path.join(ASSETS_DIR, "logo")
ASSETS_IMAGES_DIR      = os.path.join(ASSETS_DIR, "images")

# ─────────────────────────────────────────────
# LLM PROVIDER SETTINGS
# Set the keys you want to use in .env — only providers with a key are enabled.
# Groq is required (primary provider). Claude, Gemini, OpenAI are optional.
# ─────────────────────────────────────────────

# Groq (required)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = "openai/gpt-oss-120b"

# Anthropic Claude (optional)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = "claude-sonnet-4-6"

# Google Gemini (optional)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL   = "gemini-2.5-pro"

# OpenAI (optional)
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL    = "gpt-4o"

# Local (optional) — runs against a local Ollama server, no API key needed.
# Requires the Ollama runtime installed and running, and the model pulled
# (e.g. `ollama pull qwen2.5:14b-instruct`). Only touched when agent_model
# (or a provider_models entry) is explicitly set to "local" — no effect on
# any deployment that doesn't opt in.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen2.5:14b-instruct")


def get_enabled_models() -> list[str]:
    """Return provider labels for all API keys that are configured."""
    enabled = []
    if GROQ_API_KEY:      enabled.append("groq")
    if ANTHROPIC_API_KEY: enabled.append("claude")
    if GOOGLE_API_KEY:    enabled.append("gemini")
    if OPENAI_API_KEY:    enabled.append("openai")
    return enabled


# ─────────────────────────────────────────────
# ATTACHMENT CHAR BUDGET (per active provider)
# How much extracted attachment text the WRITE phase may consume. The context is
# assembled ONCE and shared across the fanout, so the effective cap is the MIN
# over the active providers. Groq's free tier is per-minute-token-limited (~12k
# TPM) so it must stay small; large-context providers (Gemini/Claude/OpenAI) can
# take far more without truncating a real document. This is what frees non-Groq
# deployments from the old hardcoded 80k straitjacket. When truncation DOES still
# happen it is disclosed (to the model and to the user) — never silent.
# ─────────────────────────────────────────────
_PROVIDER_CHAR_BUDGET = {
    "groq":   60_000,    # free tier ~12k TPM — backstopped by invoke_with_tpm_retry
    "openai": 400_000,   # gpt-4o 128k ctx
    "claude": 600_000,   # sonnet 200k ctx
    "gemini": 800_000,   # 2.5 pro 1M+ ctx
}
DEFAULT_CHAR_BUDGET = 60_000


def get_attachment_char_budget(providers=None) -> int:
    """Max chars of attachment text to feed the WRITE phase, sized to the most
    constrained ACTIVE provider (context is shared across the fanout). Pin a
    large-context provider via agent_config.yaml `agent_model` to lift the cap.
    Override outright with MAX_ATTACHMENT_CHARS in .env."""
    override = os.getenv("MAX_ATTACHMENT_CHARS", "").strip()
    if override.isdigit() and int(override) > 0:
        return int(override)
    labels = [p for p in (providers or get_enabled_models()) if p]
    if not labels:
        return DEFAULT_CHAR_BUDGET
    return min(_PROVIDER_CHAR_BUDGET.get(p, DEFAULT_CHAR_BUDGET) for p in labels)


AGENT_NAME = os.getenv("AGENT_NAME", "Sally")

# ─────────────────────────────────────────────
# AGENT CONFIG FILE (agent_config.yaml)
# All operator-tunable settings (agent name, model, vision, allowlist) live in
# this file. It is read live (re-loaded only when the file's mtime changes), so
# edits apply on the next email — no restart. Precedence: file → .env → default.
# The dashboard is retained for future use but no longer drives the agent.
# ─────────────────────────────────────────────
AGENT_CONFIG_PATH = os.getenv("AGENT_CONFIG_PATH", os.path.join(_BASE_DIR, "agent_config.yaml"))
_agent_cfg_cache = {"mtime": None, "data": {}}


def load_agent_config() -> dict:
    """Return agent_config.yaml as a dict, reloaded only when the file changes."""
    try:
        mt = os.path.getmtime(AGENT_CONFIG_PATH)
    except OSError:
        return {}
    if _agent_cfg_cache["mtime"] != mt:
        try:
            import yaml
            # utf-8-sig (not utf-8): tolerates a BOM if Notepad or another Windows
            # editor wrote one when the file was last hand-edited. (PyYAML strips a
            # leading BOM itself, so this is belt-and-braces for safe_load — but
            # set_agent_config_value()'s ruamel round-trip loader below gets the
            # same treatment, where it is not guaranteed.)
            with open(AGENT_CONFIG_PATH, encoding="utf-8-sig") as f:
                _agent_cfg_cache["data"] = yaml.safe_load(f) or {}
            _agent_cfg_cache["mtime"] = mt
            print("[CONFIG] Loaded agent_config.yaml")
        except Exception as e:
            # ASCII only, deliberately: this line reports why the config was
            # unusable, and it must survive a cp1252 stdout (Windows console or a
            # redirected/piped capture on a Python without UTF-8 mode). A non-ASCII
            # character here raises UnicodeEncodeError *inside the except handler*,
            # which replaces a readable diagnostic with an opaque crash — exactly
            # when the operator most needs to see what went wrong.
            print(f"[CONFIG] Failed to read agent_config.yaml ({e}) - using .env/defaults")
    return _agent_cfg_cache["data"]


def set_agent_config_value(key: str, value) -> None:
    """Persist one setting into agent_config.yaml — used by the Settings UI
    (ui/settings_app.py). `key` may be dotted for a nested value, e.g.
    "provider_models.groq". Uses ruamel.yaml's round-trip loader instead of
    PyYAML's safe_load/dump, so the file's extensive hand-written comments
    and formatting survive the write (a plain load+dump would silently
    delete every comment in the file). Reload is automatic: load_agent_config()
    re-parses whenever AGENT_CONFIG_PATH's mtime changes, which a write to
    the file always causes; the in-memory cache is also cleared here so the
    SAME process (e.g. the Streamlit UI itself) sees the new value immediately
    rather than waiting on mtime-comparison granularity.
    """
    from ruamel.yaml import YAML
    _yaml = YAML()
    _yaml.preserve_quotes = True
    _yaml.indent(mapping=2, sequence=4, offset=2)
    with open(AGENT_CONFIG_PATH, encoding="utf-8-sig") as f:
        data = _yaml.load(f) or {}
    parts = key.split(".")
    node = data
    for p in parts[:-1]:
        node = node[p]
    last = parts[-1]
    # A trailing comment in the file (e.g. a note above the NEXT key, like
    # allow_all_senders' explanation after sender_allowlist) is attached by
    # ruamel to ca.items[<last index>] of the SEQUENCE object, not to the
    # mapping — confirmed by inspecting the parsed tree directly. Neither a
    # reference replacement NOR a plain slice-assign preserves it (both lose
    # the old index's comment token when the list's length/identity changes),
    # so it has to be explicitly captured and re-anchored to the new last index.
    existing = node.get(last) if hasattr(node, "get") else None
    if isinstance(value, list) and isinstance(existing, list):
        old_last_idx = len(existing) - 1
        trailing_comment = None
        if hasattr(existing, "ca") and old_last_idx in existing.ca.items:
            trailing_comment = existing.ca.items.pop(old_last_idx)
        existing[:] = value
        if trailing_comment is not None and len(existing) > 0:
            existing.ca.items[len(existing) - 1] = trailing_comment
        # NARROW KNOWN LIMITATION: if the list is cleared to zero items
        # there's no remaining index to re-anchor a trailing comment to, so
        # it is dropped in that one case (tried ca.end as a fallback; ruamel
        # does not honor it for this position). Low-stakes in practice — the
        # sender_allowlist case this was found on has its critical info
        # ("empty list fails closed") in the LEADING comment above the list,
        # which is untouched either way.
    else:
        node[last] = value
    with open(AGENT_CONFIG_PATH, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)
    _agent_cfg_cache["mtime"] = None


def get_agent_name() -> str:
    """Agent display name — agent_config.yaml → env AGENT_NAME → 'Sally'.
    Used in signatures, prompts, and the CC 'addressed?' check."""
    return (load_agent_config().get("agent_name") or AGENT_NAME or "Sally").strip()


_DEFAULT_COMPANY = {
    "name":  "EAI Systems Pvt. Ltd.",
    "short": "EAI SYSTEMS",
    "email": "info@eaisystems.com",
    "web":   "www.eaisystems.com",
}


def get_company_info() -> dict:
    """Company identity shown in signatures, generated documents/PPTs, and
    the LLM's own persona in write prompts — agent_config.yaml's `company:`
    block, each field independently falling back to today's EAI defaults so
    an existing deployment that never sets this is unaffected."""
    cfg = load_agent_config().get("company") or {}
    return {k: (str(cfg.get(k) or "").strip() or v) for k, v in _DEFAULT_COMPANY.items()}


def brand_text(text: str) -> str:
    """Substitute the hardcoded EAI Systems identity in a static prompt
    constant (e.g. _WRITE_PROMPTS) with the configured company's, applied at
    the point the prompt is fetched for use — keeps the prompt constants
    themselves as simple, readable, diffable strings rather than f-strings
    rebuilt at import time (which wouldn't hot-reload a dashboard change)."""
    info = get_company_info()
    return (text
            .replace(_DEFAULT_COMPANY["name"], info["name"])
            .replace(_DEFAULT_COMPANY["web"], info["web"])
            .replace(_DEFAULT_COMPANY["email"], info["email"]))


def get_email_signature(agent_name: str = None) -> str:
    """Shared email/document signature block — replaces what used to be the
    same hardcoded f-string duplicated independently in 9 places across the
    codebase, so a company-identity change can't be missed at one of them."""
    info = get_company_info()
    return f"Best regards,\n{agent_name or get_agent_name()} | {info['name']}\n{info['web']}"


def is_ppt_logo_enabled() -> bool:
    """Check if company logo should be included in generated presentations.
    Precedence: agent_config.yaml `ppt_include_logo` -> .env PPT_INCLUDE_LOGO -> True."""
    cfg = load_agent_config()
    if "ppt_include_logo" in cfg:
        val = cfg["ppt_include_logo"]
        return bool(val) if not isinstance(val, str) else val.strip().lower() in ("true", "1", "yes")
    env_val = os.getenv("PPT_INCLUDE_LOGO", "true").strip().lower()
    return env_val in ("true", "1", "yes")


def get_ollama_models() -> list:
    """Names of models currently pulled in the local Ollama server (for the
    Settings dashboard's local-model dropdowns). Returns [] on any failure
    (Ollama not installed/running, network error, malformed response) —
    never raises, same optional-dependency philosophy as the rest of this
    codebase's local-model support (it's opt-in only, so a machine without
    Ollama running must not break anything that merely checks for it)."""
    try:
        import requests
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        resp.raise_for_status()
        return [m.get("name") for m in (resp.json().get("models") or []) if m.get("name")]
    except Exception:
        return []


def get_agent_model() -> str:
    """Pinned provider for replies ('' = Auto / multi-model). From agent_config.yaml."""
    return str(load_agent_config().get("agent_model") or "").strip()


def get_vision_provider() -> str:
    """Vision provider for image/logo tagging — agent_config.yaml → env → ''."""
    return str(load_agent_config().get("vision_provider") or VISION_PROVIDER or "").strip()


def get_provider_model(provider: str) -> str:
    """Exact model variant for a provider — agent_config.yaml provider_models → default."""
    pm = load_agent_config().get("provider_models") or {}
    if pm.get(provider):
        return str(pm[provider]).strip()
    if provider == "groq": return GROQ_MODEL
    if provider == "claude": return CLAUDE_MODEL
    if provider == "gemini": return GEMINI_MODEL
    if provider == "openai": return OPENAI_MODEL
    if provider == "local": return OLLAMA_MODEL
    if provider == "local_vision": return OLLAMA_VISION_MODEL
    return ""


def get_routing_model() -> str:
    """LLM provider used for the email routing decision (which agent is this for?).
    Falls back to the first enabled provider if not set."""
    val = str(load_agent_config().get("routing_model") or "").strip()
    if val:
        return val
    enabled = get_enabled_models()
    return enabled[0] if enabled else "groq"


def get_known_agents() -> list:
    """Other assistant agents sharing this mailbox (from agent_config.yaml).

    Reads the `assistant_agents` key; falls back to the legacy `known_agents`
    key so older config files keep working.
    """
    cfg = load_agent_config()
    raw = cfg.get("assistant_agents")
    if raw is None:
        raw = cfg.get("known_agents") or []
    return [str(a).strip() for a in raw if str(a).strip()]


# Built-in default for the meeting/reply domain guardrail. If agent_config.yaml
# has no `meeting_attendee_domains`, the guardrail still restricts to this — it
# can never silently turn off (fail-safe). Add more domains in the config to
# allow them; change this default if the deployment's primary domain differs.
DEFAULT_ALLOWED_DOMAINS = ["eaisystems.com"]


def get_meeting_allowed_domains() -> list:
    """Email domains the agent may invite to MEETINGS and CC on REPLIES
    (agent_config.yaml `meeting_attendee_domains`). Anyone outside these is never
    contacted. If the config is empty/unset it falls back to
    DEFAULT_ALLOWED_DOMAINS (eaisystems.com) so the guardrail is always active."""
    raw = load_agent_config().get("meeting_attendee_domains") or []
    domains = [str(d).strip().lower().lstrip("@") for d in raw if str(d).strip()]
    return domains or list(DEFAULT_ALLOWED_DOMAINS)

# ─────────────────────────────────────────────
# MULTI-AGENT MODEL ASSIGNMENTS (v3)
# Each subagent can run on a different LLM provider.
# Defaults to "groq" (fastest / free-tier) unless overridden.
# ─────────────────────────────────────────────
SUPERVISOR_MODEL       = os.getenv("SUPERVISOR_MODEL",       "gemini" if GOOGLE_API_KEY else "groq")
RESEARCH_AGENT_MODEL   = os.getenv("RESEARCH_AGENT_MODEL",   "groq")
PROPOSAL_AGENT_MODEL   = os.getenv("PROPOSAL_AGENT_MODEL",   "claude") if ANTHROPIC_API_KEY else "groq"
PPT_AGENT_MODEL        = os.getenv("PPT_AGENT_MODEL",        "groq")
DOCUMENT_ANALYST_MODEL = os.getenv("DOCUMENT_ANALYST_MODEL", "groq")
QUICK_REPLY_MODEL      = os.getenv("QUICK_REPLY_MODEL",      "groq")

# ─────────────────────────────────────────────
# VISION MODEL SETTINGS  (used for auto-tagging images and logos in the asset library)
# VISION_PROVIDER: groq | gemini | claude | openai | local
# VISION_MODEL:    leave blank to use the default for the chosen cloud provider
# ─────────────────────────────────────────────
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "groq")
VISION_MODEL    = os.getenv("VISION_MODEL",    "")

# Local vision model — runs against the same Ollama server as agent_model/
# routing_model "local" (OLLAMA_BASE_URL above), but needs its OWN model tag
# since a plain text model (e.g. qwen2.5:14b-instruct) isn't multimodal. Only
# touched when vision_provider is explicitly set to "local" — same opt-in-only
# rule as agent_model/routing_model (never auto-selected as a fallback).
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")

# ─────────────────────────────────────────────
# EMBEDDING MODEL SETTINGS
# ─────────────────────────────────────────────
# Served locally via Ollama (same runtime as agent_model: local — see above),
# regardless of which provider is pinned for replies, so knowledge-base content
# never has to leave the machine just to be embedded. bge-m3 is multilingual
# (~100 languages incl. Arabic), so English and Arabic content share one KB
# and retrieve across languages.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3")

# ─────────────────────────────────────────────
# CHROMADB / VECTOR STORE SETTINGS
# ─────────────────────────────────────────────
# Lives inside DATABASE_DIR (a "chroma_db" subfolder) — same shared, persists-
# across-upgrades location used for the SQLite DB (see DB_FILENAME below),
# just a different file within it. Override CHROMA_PERSIST_DIR directly only
# if the vector store specifically needs to live somewhere else on its own.
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR") or os.path.join(DATABASE_DIR, "chroma_db")
# Deliberately NOT "rag_docs" — older deployments (this codebase's now-deleted
# original RAG system) can have a stale "rag_docs" collection left over in this
# same chroma_db/ directory, built with a different embedding model/dimension
# (768-d bge-base-en-v1.5 vs. this system's 1024-d bge-m3). Reusing that name
# hits Chroma's "embedding dimension mismatch" error on the very first real
# ingest and silently disables RAG. A distinct name sidesteps the collision
# without touching/deleting whatever old data might still be sitting there.
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "phantomops_kb")

# ─────────────────────────────────────────────
# FILE PATHS
# ─────────────────────────────────────────────
DATA_DIR = os.path.join(_BASE_DIR, "data")

# ─────────────────────────────────────────────
# RAG SETTINGS
# ─────────────────────────────────────────────
# Chunk target: structure-aware chunks are packed up to this many characters.
CHUNK_SIZE = 500
# Fallback overlap for oversized paragraphs/sections split inside chunking.py.
CHUNK_OVERLAP = 100
# Top-K: retrieve the 5 most relevant chunks per query
TOP_K_RETRIEVAL = 5
# Minimum relevance score (0..1, cosine similarity = 1 - distance) a retrieved
# chunk must clear to be USED. Below this, a chunk is dropped as not-actually-
# relevant so unrelated knowledge-base content doesn't leak into a reply — e.g.
# a bare "hi" would otherwise still pull the top-K chunks of whatever's in the
# KB and the model would ramble about them. Measured with bge-m3: genuinely
# relevant matches (incl. cross-language EN<->AR) score ~0.65+, while unrelated
# greetings top out ~0.43, so 0.5 sits cleanly in the gap. Env-overridable
# (lower it if legitimately-relevant hits are being filtered, raise it if junk
# still leaks through).
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.5"))

# ─────────────────────────────────────────────
# MYSQL SETTINGS (centralized company server)
# ─────────────────────────────────────────────
# DATABASE — SQLite only. All data lives in {DATABASE_DIR}/{DB_FILENAME}
# (one file per agent). No database server, no Redis. Zero installation.
# DB_FILENAME defaults to "sally.db" (existing deployments keep their data);
# set DB_FILENAME in .env to rebrand the file (e.g. phantomops.db).
# ─────────────────────────────────────────────
DB_FILENAME = (os.getenv("DB_FILENAME") or "sally.db").strip()

# ─────────────────────────────────────────────
# EMAIL PROVIDER — "outlook" (default) or "gmail"
# Switch backends with one setting; both implementations coexist.
# ─────────────────────────────────────────────
def get_email_provider() -> str:
    """Active mailbox backend — agent_config.yaml `email_provider` → .env
    EMAIL_PROVIDER → 'outlook'. UNLIKE agent_model/vision_provider, this is
    NOT hot-reloaded: skills/email_skill/email_client.py picks the client
    module at IMPORT time, and run_agent.py authenticates once at startup —
    so a change here only takes effect after restarting the agent process."""
    cfg = load_agent_config()
    val = str(cfg.get("email_provider") or "").strip().lower()
    if val in ("outlook", "gmail"):
        return val
    return os.getenv("EMAIL_PROVIDER", "outlook").lower().strip()


EMAIL_PROVIDER      = get_email_provider()
EMAIL_POLL_INTERVAL = int(os.getenv("EMAIL_POLL_INTERVAL", "30"))  # seconds

# How often (seconds) the main loop re-scans INTAKE_DIR to auto-ingest any new
# file into the RAG KB. Separate from EMAIL_POLL_INTERVAL — the intake folder
# doesn't need checking as often as the mailbox, and each scan does a cheap
# dedup lookup per file (skips anything already ingested) so a longer interval
# just means new drops take a little longer to become searchable, nothing breaks.
RAG_INTAKE_SCAN_INTERVAL_SEC = int(os.getenv("RAG_INTAKE_SCAN_INTERVAL_SEC", "120"))

# Watermark safety-lag (seconds). The downtime watermark fetches mail received
# at/after the last handled timestamp — but mail delivery is NOT monotonic: a
# message can be STAMPED before the watermark yet become visible to the server
# only after the watermark has already advanced past it (Exchange/Graph indexing
# lag, slow upstream relays). A `>= watermark` filter would skip it forever.
# Fix: fetch from `watermark - lag`, re-scanning a small recent window each poll
# so late-arriving-but-earlier-stamped mail is still caught. The re-scanned
# window is deduped cheaply (seen_ids + draft_exists) before any work is done.
# Raise it if your mail path has high delivery latency; 0 disables the lookback.
EMAIL_WATERMARK_LAG_SEC = int(os.getenv("EMAIL_WATERMARK_LAG_SEC", "120"))

# Transient-provider-overload auto-retry (LLMTransientError — 503/UNAVAILABLE/
# "high demand"). When every enabled provider fails transiently on one email,
# the agent defers instead of replying: no message is sent, the watermark
# doesn't advance, and the poll loop retries the SAME email automatically once
# its backoff window elapses (tracked per-message in the config table — see
# set_retry_state). After MAX_TRANSIENT_RETRIES attempts it gives up and sends
# one honest "please resend" notice instead of retrying forever.
MAX_TRANSIENT_RETRIES = int(os.getenv("MAX_TRANSIENT_RETRIES", "5"))
# Backoff per attempt number (1-indexed) in seconds: 2, 5, 10, 20, 30 minutes.
# An attempt beyond the list length reuses the last (longest) interval.
TRANSIENT_RETRY_BACKOFF_SEC = [120, 300, 600, 1200, 1800]


def transient_retry_backoff_seconds(attempt: int) -> int:
    """Backoff delay before auto-retrying a transient provider failure, given
    the 1-indexed attempt number just recorded."""
    idx = max(0, min(attempt, len(TRANSIENT_RETRY_BACKOFF_SEC)) - 1)
    return TRANSIENT_RETRY_BACKOFF_SEC[idx]

# ─────────────────────────────────────────────
# MEETING / CALENDAR SETTINGS
# Sally schedules into Google Calendar (gmail) or Outlook Calendar (outlook),
# selected by EMAIL_PROVIDER. Times the user gives ("tomorrow 3pm") are
# interpreted in MEETING_TIMEZONE.
# ─────────────────────────────────────────────
MEETING_TIMEZONE             = os.getenv("MEETING_TIMEZONE", "Asia/Kolkata")
MEETING_DEFAULT_DURATION_MIN = int(os.getenv("MEETING_DEFAULT_DURATION_MIN", "30"))

# ─────────────────────────────────────────────
# OUTLOOK SETTINGS
# Run `python skills/email_skill/setup_outlook_oauth.py` once to generate these values.
# ─────────────────────────────────────────────
OUTLOOK_CLIENT_ID     = os.getenv("OUTLOOK_CLIENT_ID",     "")
OUTLOOK_CLIENT_SECRET = os.getenv("OUTLOOK_CLIENT_SECRET", "")
OUTLOOK_TENANT_ID     = os.getenv("OUTLOOK_TENANT_ID",     "")

# ─────────────────────────────────────────────
# GMAIL SETTINGS (Gmail API + OAuth)
# GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET come from a Google Cloud "Desktop app"
# OAuth client — mirrors OUTLOOK_CLIENT_ID/_SECRET/_TENANT_ID above: paste the
# two plain values into .env, no credentials.json file to download/place.
# Run `python skills/email_skill/setup_gmail_oauth.py` once to generate gmail_token.json.
# ─────────────────────────────────────────────
GMAIL_CLIENT_ID     = os.getenv("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")
GMAIL_TOKEN_PATH    = os.getenv("GMAIL_TOKEN_PATH", os.path.join(_BASE_DIR, "gmail_token.json"))

# ─────────────────────────────────────────────
# SENDER ALLOWLIST — who may trigger Sally
# Comma-separated full emails and/or whole domains ("@eaisystems.com").
# Setting this in .env makes Sally ignore any email from an unlisted sender,
# so an arbitrary inbound message can no longer drive the agent pipeline.
#
# SECURITY DEFAULT — fail CLOSED: if NO allowlist is configured, Sally replies
# to NOBODY. An autonomous agent that sends mail, books meetings, and writes
# files must not default to "reply to anyone on the internet". To deliberately
# run open (reply to every sender), set ALLOW_ALL_SENDERS=true in .env or
# `allow_all_senders: true` in agent_config.yaml — an explicit, logged choice.
# ─────────────────────────────────────────────
SENDER_ALLOWLIST = [
    s.strip().lower()
    for s in os.getenv("SENDER_ALLOWLIST", "").split(",")
    if s.strip()
]


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def get_allow_all_senders() -> bool:
    """Explicit opt-in to reply to ANY sender when no allowlist is set.
    Precedence: agent_config.yaml `allow_all_senders` → .env ALLOW_ALL_SENDERS
    → False (fail closed). Only consulted when the effective allowlist is empty."""
    cfg = load_agent_config()
    if "allow_all_senders" in cfg:
        return bool(cfg.get("allow_all_senders"))
    return _env_truthy("ALLOW_ALL_SENDERS")


def get_effective_allowlist() -> tuple[list, str]:
    """Return (allowlist, source) actually in force.
    Precedence: agent_config.yaml `sender_allowlist` → .env SENDER_ALLOWLIST.
    """
    cfg = load_agent_config()
    if isinstance(cfg.get("sender_allowlist"), list):
        return [str(s).strip().lower() for s in cfg["sender_allowlist"] if str(s).strip()], "agent_config.yaml"
    return list(SENDER_ALLOWLIST), ".env"


def is_sender_allowed(sender: str) -> bool:
    """True if `sender` may trigger the agent. Uses the effective allowlist
    (agent_config.yaml → .env). When the allowlist is EMPTY the agent fails
    CLOSED (denies everyone) unless `allow_all_senders` is explicitly enabled."""
    allow, _ = get_effective_allowlist()
    if not allow:
        return get_allow_all_senders()
    sender_l = (sender or "").lower()
    domain   = sender_l.rsplit("@", 1)[-1]
    return sender_l in allow or f"@{domain}" in allow
