"""
tools/asset_manager.py — Unified dynamic asset library

Scans all four asset subfolders and maintains a single index.json.
New files are auto-tagged; deleted files are pruned on every scan.

Categories and tagging strategy:
  images      — vision LLM (llama-4-scout) → rich tags + description
  logos       — vision LLM                  → brand/variant tags
  templates   — filename parsing             → purpose + color tags
  letterheads — filename parsing             → region + language tags

Public API:
  scan_all()                → index new files in every category
  get_best_template(query)  → .pptx path
  get_best_letterhead(query)→ .docx path
  get_logo(prefer="jpg")    → logo image path (skips SVG by default)
  get_best_image(query)     → slide image path
  get_template_summary()    → one-paragraph text for LLM prompts
  get_letterhead_summary()  → one-paragraph text for LLM prompts
  get_asset_summary()       → image library blurb for LLM prompts
"""

import base64
import json
import os
import re
from collections import Counter
from datetime import datetime

from core.config import (
    ASSETS_DIR, GROQ_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENAI_BASE_URL,
    VISION_PROVIDER, VISION_MODEL, OLLAMA_BASE_URL,
    ASSETS_TEMPLATES_DIR, ASSETS_LETTERHEADS_DIR,
    ASSETS_LOGOS_DIR, ASSETS_IMAGES_DIR,
)

_INDEX_PATH = os.path.join(ASSETS_DIR, "index.json")
_MAX_BYTES  = 4 * 1024 * 1024   # 4 MB upload limit

_DEFAULT_VISION_MODELS = {
    "groq":      "qwen/qwen3.6-27b",
    "gemini":    "gemini-1.5-flash",
    "claude":    "claude-3-haiku-20240307",
    "openai":    "gpt-4o-mini",
}

ASSET_DIRS = {
    "templates":   ASSETS_TEMPLATES_DIR,
    "letterheads": ASSETS_LETTERHEADS_DIR,
    "logos":       ASSETS_LOGOS_DIR,
    "images":      ASSETS_IMAGES_DIR,
}

ASSET_EXTS = {
    "templates":   {".pptx"},
    "letterheads": {".docx"},
    "logos":       {".jpg", ".jpeg", ".png", ".svg", ".webp"},
    "images":      {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"},
}

# Vision is used for categories where filename alone is insufficient
_VISION_CATEGORIES = {"images", "logos"}


# ── Index persistence ─────────────────────────────────────────────────────────

def _load_index() -> dict:
    if os.path.isfile(_INDEX_PATH):
        try:
            with open(_INDEX_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": 2, "assets": {c: {} for c in ASSET_DIRS}}


def _save_index(index: dict) -> None:
    os.makedirs(ASSETS_DIR, exist_ok=True)
    with open(_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


# ── Tagging helpers ───────────────────────────────────────────────────────────

def _stem_words(path: str) -> list[str]:
    """Split filename stem into lowercase words, filtering noise."""
    stem  = os.path.splitext(os.path.basename(path))[0]
    words = re.split(r"[\s_\-|&]+", stem.lower())
    noise = {"a", "b", "c", "the", "and", "for", "of", "in", "to",
             "template", "rating", "format", "letter", "head", "ltd", "pvt"}
    return [w for w in words if len(w) > 1 and w not in noise]


def _tag_template(path: str) -> dict:
    words = _stem_words(path)
    purpose_map = {
        "sales":     "sales presentation",
        "customer":  "client-facing",
        "customers": "client-facing",
        "marketing": "marketing campaign",
        "event":     "event or product launch",
        "events":    "event or product launch",
        "internal":  "internal report",
        "technical": "technical documentation",
        "blue":      "blue theme",
        "orange":    "orange theme",
        "dark":      "dark theme",
    }
    purposes = list(dict.fromkeys(purpose_map[w] for w in words if w in purpose_map))
    return {
        "tags":        words,
        "description": " | ".join(purposes) if purposes else " ".join(words),
        "purpose":     ", ".join(purposes) if purposes else "general presentation",
    }


def _tag_letterhead(path: str) -> dict:
    words = _stem_words(path)
    region_map = {
        "india": "india",
        "uk":    "uk",
        "us":    "us",
        "usa":   "us",
        "eu":    "europe",
        "europe":"europe",
    }
    region = next((region_map[w] for w in words if w in region_map), "default")
    return {
        "tags":        words + [region],
        "description": f"{region.title()} region letterhead",
        "region":      region,
    }


def _mime_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif",  "webp": "image/webp",  "bmp": "image/bmp"}.get(ext, "image/jpeg")


def _tag_with_groq(path: str, prompt: str, model: str) -> dict | None:
    if not GROQ_API_KEY:
        return None
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        mime = _mime_for(path)
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        from groq import Groq
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]}],
            temperature=0, max_tokens=800,
        )
        raw = resp.choices[0].message.content or ""
        match = re.search(r"\{[\s\S]*?\}", raw)
        return json.loads(match.group()) if match else None
    except Exception as e:
        print(f"[Assets] Groq vision failed for {os.path.basename(path)}: {e}")
        return None


def _tag_with_gemini(path: str, prompt: str, model: str) -> dict | None:
    if not GOOGLE_API_KEY:
        return None
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        mime = _mime_for(path)
        with open(path, "rb") as f:
            img_bytes = f.read()
        import google.generativeai as genai
        genai.configure(api_key=GOOGLE_API_KEY)
        m = genai.GenerativeModel(model)
        resp = m.generate_content([{"mime_type": mime, "data": img_bytes}, prompt])
        raw = resp.text or ""
        match = re.search(r"\{[\s\S]*?\}", raw)
        return json.loads(match.group()) if match else None
    except Exception as e:
        print(f"[Assets] Gemini vision failed for {os.path.basename(path)}: {e}")
        return None


def _tag_with_claude(path: str, prompt: str, model: str) -> dict | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        mime = _mime_for(path)
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text",  "text": prompt},
            ]}],
        )
        raw = resp.content[0].text if resp.content else ""
        match = re.search(r"\{[\s\S]*?\}", raw)
        return json.loads(match.group()) if match else None
    except Exception as e:
        print(f"[Assets] Claude vision failed for {os.path.basename(path)}: {e}")
        return None


def _tag_with_openai(path: str, prompt: str, model: str) -> dict | None:
    if not OPENAI_API_KEY:
        return None
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        mime = _mime_for(path)
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text",      "text": prompt},
            ]}],
        )
        raw = resp.choices[0].message.content or ""
        match = re.search(r"\{[\s\S]*?\}", raw)
        return json.loads(match.group()) if match else None
    except Exception as e:
        print(f"[Assets] OpenAI vision failed for {os.path.basename(path)}: {e}")
        return None


def _tag_with_local(path: str, prompt: str, model: str) -> dict | None:
    """Vision tagging via a local Ollama multimodal model (e.g. llava). No API
    key needed — requires Ollama running with `model` pulled."""
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        import requests
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt, "images": [b64]}],
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = (resp.json().get("message") or {}).get("content", "")
        match = re.search(r"\{[\s\S]*?\}", raw)
        return json.loads(match.group()) if match else None
    except Exception as e:
        print(f"[Assets] Local (Ollama) vision failed for {os.path.basename(path)}: {e}")
        return None


def _tag_with_vision(path: str, prompt: str) -> dict | None:
    """
    Auto-select the vision provider based on what's configured.
    If VISION_PROVIDER is set in .env, try that first.
    Falls back through all available providers until one succeeds.
    """
    _PROVIDERS = [
        ("groq",   GROQ_API_KEY,      _tag_with_groq),
        ("gemini", GOOGLE_API_KEY,    _tag_with_gemini),
        ("claude", ANTHROPIC_API_KEY, _tag_with_claude),
        ("openai", OPENAI_API_KEY,    _tag_with_openai),
    ]

    # Vision provider from agent_config.yaml (falls back to .env VISION_PROVIDER)
    try:
        from core.config import get_vision_provider
        preferred = (get_vision_provider() or "").lower()
    except Exception:
        preferred = (VISION_PROVIDER or "").lower()

    # Build ordered list: preferred first, then others
    ordered = []
    for name, key, fn in _PROVIDERS:
        if key:
            if name == preferred:
                ordered.insert(0, (name, fn))
            else:
                ordered.append((name, fn))

    # "local" (Ollama) has no API key to gate on, unlike the cloud providers
    # above — so it's opt-in only, same rule as agent_model/routing_model:
    # included ONLY when explicitly pinned as vision_provider, never as an
    # automatic fallback (a machine without Ollama running would otherwise
    # silently stall/fail every asset-tagging call trying to reach it).
    if preferred == "local":
        ordered.insert(0, ("local", _tag_with_local))

    if not ordered:
        print("[Assets] No vision provider available — image tagging skipped.")
        return None

    for name, fn in ordered:
        if name == "local":
            from core.config import get_provider_model
            model = get_provider_model("local_vision")
        else:
            model = VISION_MODEL if (VISION_PROVIDER or "").lower() == name and VISION_MODEL \
                    else _DEFAULT_VISION_MODELS.get(name, "")
        print(f"[Assets] Vision tagging via {name} ({model}): {os.path.basename(path)}")
        result = fn(path, prompt, model)
        if result:
            return result
        if name != ordered[-1][0]:
            print(f"[Assets] {name} vision failed — trying next provider...")

    return None


def _tag_image(path: str) -> dict:
    result = _tag_with_vision(path, (
        "Return ONLY JSON — no markdown:\n"
        '{"description":"1-2 sentence description","tags":["tag1","tag2"...],"type":"photo|icon|chart|illustration"}'
    ))
    if result:
        return {
            "description": str(result.get("description", "")),
            "tags":        [str(t).lower() for t in result.get("tags", []) if t],
            "type":        str(result.get("type", "photo")),
            "tag_source":  "vision",
        }
    # Vision tagging failed (no provider configured, API error, rate limit,
    # oversized image, non-JSON response...) — fall back to the filename, but
    # mark it as a guess. A filename is not a description of what's IN the
    # image (e.g. "acme_project_kickoff.jpg" says nothing about the photo),
    # so this must not be trusted the same as a real vision-derived tag —
    # see get_asset_summary() and scan_all(), which both key off tag_source.
    words = _stem_words(path)
    return {"description": " ".join(words), "tags": words, "type": "photo", "tag_source": "filename"}


def _tag_logo(path: str) -> dict:
    result = _tag_with_vision(path, (
        "Return ONLY JSON — no markdown:\n"
        '{"description":"1-2 sentence description of this logo","tags":["tag1","tag2"...],'
        '"background":"light|dark","color_scheme":"color description"}'
    ))
    words = _stem_words(path)
    if result:
        return {
            "description":   str(result.get("description", "")),
            "tags":          [str(t).lower() for t in result.get("tags", []) if t],
            "background":    str(result.get("background", "light")),
            "color_scheme":  str(result.get("color_scheme", "")),
        }
    return {"description": " ".join(words), "tags": words, "background": "light", "color_scheme": ""}


def _tag_asset(category: str, path: str) -> dict:
    if category == "images":
        return _tag_image(path)
    if category == "logos":
        return _tag_logo(path)
    if category == "templates":
        return _tag_template(path)
    if category == "letterheads":
        return _tag_letterhead(path)
    return {"description": os.path.basename(path), "tags": _stem_words(path)}


# ── Scan ──────────────────────────────────────────────────────────────────────

def scan_all(verbose: bool = True) -> dict[str, int]:
    """
    Scan all asset subfolders for new or deleted files and update the index.
    Returns a dict of {category: new_count}.
    """
    index  = _load_index()
    assets = index.setdefault("assets", {c: {} for c in ASSET_DIRS})
    totals: dict[str, int] = {}

    for category, folder in ASSET_DIRS.items():
        cat_assets = assets.setdefault(category, {})
        exts       = ASSET_EXTS[category]
        new_count  = 0

        if not os.path.isdir(folder):
            totals[category] = 0
            continue

        for fname in sorted(os.listdir(folder)):
            if os.path.splitext(fname)[1].lower() not in exts:
                continue
            full_path = os.path.join(folder, fname)

            if fname in cat_assets:
                # Refresh path (may have moved)
                cat_assets[fname]["path"] = full_path
                # Retry vision tagging on a later scan for an entry that only
                # ever got the filename-derived fallback (a transient vision
                # failure the first time it was indexed) — otherwise that one
                # bad moment poisons this image's metadata permanently.
                if cat_assets[fname].get("tag_source") != "filename":
                    continue
                if verbose:
                    print(f"[Assets/{category}] Retrying vision tag (previous attempt used filename fallback): {fname}")
                cat_assets[fname].update(_tag_asset(category, full_path))
                continue

            if verbose:
                print(f"[Assets/{category}] Indexing: {fname}")

            meta = _tag_asset(category, full_path)
            cat_assets[fname] = {
                "path":       full_path,
                "indexed_at": datetime.now().isoformat(timespec="seconds"),
                **meta,
            }
            new_count += 1

        # Prune stale entries
        stale = [k for k, v in cat_assets.items() if not os.path.isfile(v.get("path", ""))]
        for k in stale:
            del cat_assets[k]
            if verbose:
                print(f"[Assets/{category}] Pruned: {k}")

        totals[category] = new_count
        if verbose:
            msg = f"{new_count} new" if new_count else "up to date"
            print(f"[Assets/{category}] {msg} — {len(cat_assets)} total")

    _save_index(index)
    return totals


# ── Search ────────────────────────────────────────────────────────────────────

def _score(meta: dict, words: list[str]) -> int:
    """Score an asset entry against a list of query words."""
    tags_text = " ".join(meta.get("tags", []))
    desc_text = (meta.get("description", "") + " " + meta.get("purpose", "") +
                 " " + meta.get("region", "") + " " + meta.get("color_scheme", "")).lower()
    return (
        sum(2 for w in words if w in tags_text) +
        sum(1 for w in words if w in desc_text)
    )


def search_category(category: str, query: str, top_k: int = 3, fallback: bool = True) -> list[str]:
    """Return top-k file paths from a category matching the query.

    fallback=True (default — templates/letterheads, which always need SOME
    asset to render at all): if nothing scores > 0, falls back to the best/
    first available asset anyway. fallback=False (images, which are optional
    decoration): returns [] when nothing genuinely matches, instead of
    forcing an unrelated image onto a slide just because the library isn't
    empty."""
    index     = _load_index()
    cat       = index.get("assets", {}).get(category, {})
    words     = [w.lower() for w in re.split(r"\s+", query.strip()) if len(w) > 1]
    if not words:
        # No query — return all valid paths
        return [v["path"] for v in cat.values() if os.path.isfile(v.get("path", ""))][:top_k]

    scored = []
    for meta in cat.values():
        path = meta.get("path", "")
        if not os.path.isfile(path):
            continue
        s = _score(meta, words)
        scored.append((s, path))

    scored.sort(key=lambda x: (-x[0], x[1]))
    results = [p for s, p in scored if s > 0]
    if results:
        return results[:top_k]
    return ([p for _, p in scored] if fallback else [])[:top_k]


# ── Public getters ────────────────────────────────────────────────────────────

def get_best_template(query: str = "") -> str | None:
    """Return the .pptx path that best matches the query (e.g. 'blue sales client')."""
    results = search_category("templates", query, top_k=1)
    if results:
        print(f"[Assets] Template selected: {os.path.basename(results[0])} for query '{query}'")
    return results[0] if results else None


def get_best_letterhead(query: str = "") -> str | None:
    """Return the .docx letterhead path that best matches region/context."""
    results = search_category("letterheads", query, top_k=1)
    if results:
        print(f"[Assets] Letterhead selected: {os.path.basename(results[0])} for query '{query}'")
    return results[0] if results else None


def get_logo(prefer: str = "jpg") -> str | None:
    """
    Return a logo path, preferring the specified format extension.
    Skips SVG by default since python-pptx cannot embed SVG.
    """
    index    = _load_index()
    logos    = index.get("assets", {}).get("logos", {})
    valid    = {k: v for k, v in logos.items() if os.path.isfile(v.get("path", ""))}
    if not valid:
        return None
    # Prefer the requested format; fall back to any non-SVG; then any file
    for ext_filter in [prefer, "png", "jpg", "jpeg", "webp", None]:
        for fname, meta in valid.items():
            if ext_filter is None or fname.lower().endswith(f".{ext_filter}"):
                return meta["path"]
    return None


def get_best_image(query: str) -> str | None:
    """Return the best slide image path for a keyword query, or None if
    nothing in the library genuinely matches. An image is optional decoration
    on a slide, so "no good match" must degrade to no image — never to a
    random unrelated one just because the library isn't empty."""
    if not query:
        return None
    results = search_category("images", query, top_k=1, fallback=False)
    return results[0] if results else None


# ── Summaries for LLM prompts ─────────────────────────────────────────────────

def get_template_summary() -> str:
    """Return a text description of available templates for injection into LLM prompts."""
    index = _load_index()
    items = index.get("assets", {}).get("templates", {})
    if not items:
        return "No presentation templates available."
    lines = []
    for fname, meta in items.items():
        purpose = meta.get("purpose") or meta.get("description") or fname
        lines.append(f"  - {fname}: {purpose}")
    return "Available presentation templates:\n" + "\n".join(lines)


def get_letterhead_summary() -> str:
    """Return a text description of available letterheads for injection into LLM prompts."""
    index = _load_index()
    items = index.get("assets", {}).get("letterheads", {})
    if not items:
        return "No letterhead templates available."
    lines = []
    for fname, meta in items.items():
        region = meta.get("region") or meta.get("description") or fname
        lines.append(f"  - {fname}: {region}")
    return "Available letterheads:\n" + "\n".join(lines)


def get_asset_summary() -> str:
    """Return a text description of the image library for injection into LLM prompts."""
    index  = _load_index()
    items  = index.get("assets", {}).get("images", {})
    if not items:
        return "Image library is empty — omit image_query and image layout fields."
    type_counts: dict[str, int] = {}
    all_tags: list[str] = []
    for meta in items.values():
        t = meta.get("type", "photo")
        type_counts[t] = type_counts.get(t, 0) + 1
        # Filename-derived tags (vision tagging failed for this image) are a
        # guess, not a verified visual theme — surfacing them here would let
        # an unrelated filename (a client name, a project codename) pose as
        # an available library theme and skew what the model asks for.
        if meta.get("tag_source") != "filename":
            all_tags.extend(meta.get("tags", []))
    type_summary = ", ".join(f"{v} {k}(s)" for k, v in sorted(type_counts.items(), key=lambda x: -x[1]))
    top_tags     = [t for t, _ in Counter(all_tags).most_common(15)]
    theme_line = (
        f"Common themes available: {', '.join(top_tags)}."
        if top_tags else
        "Themes not yet catalogued — pick any relevant image_query; the "
        "closest available image will be used if one genuinely matches."
    )
    return f"Image library: {len(items)} images — {type_summary}.\n{theme_line}"
