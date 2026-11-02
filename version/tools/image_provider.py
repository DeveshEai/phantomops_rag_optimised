"""
tools/image_provider.py — Dedicated Real Image Retrieval & Caching Engine

Provides context-aware photograph retrieval for Marp presentations.
Supports:
  1. Priority resolution of matching local assets (via tools.asset_manager)
  2. Free real-image search & retrieval via the Pexels API (PEXELS_API_KEY)
  3. Persistent disk caching and metadata indexing (assets/images/cache)
  4. Byte-level image validation (JPEG, PNG, WebP) and corruption prevention
  5. Graceful fallback on missing keys, rate limits, timeouts, or empty results
"""

import os
import re
import json
import hashlib
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from typing import Optional, Dict, Any

from core.config import (
    PEXELS_API_KEY,
    ASSETS_IMAGES_CACHE_DIR,
    ASSETS_DIR,
)

# API Endpoint & Defaults
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
DEFAULT_TIMEOUT_SEC = 10
_METADATA_FILE = "metadata.json"

# Magic byte signatures for validating image integrity
_IMAGE_SIGNATURES = [
    (b"\xff\xd8\xff", "jpg"),               # JPEG / JFIF
    (b"\x89PNG\r\n\x1a\n", "png"),          # PNG
    (b"RIFF", "webp"),                      # WebP (starts with RIFF, bytes 8-12 WEBP)
    (b"GIF87a", "gif"),                     # GIF
    (b"GIF89a", "gif"),                     # GIF
]


def _ensure_cache_dir() -> str:
    """Ensure cache directory exists and return normalized path."""
    os.makedirs(ASSETS_IMAGES_CACHE_DIR, exist_ok=True)
    return ASSETS_IMAGES_CACHE_DIR


def _get_metadata_path() -> str:
    """Return path to cache metadata JSON index."""
    return os.path.join(_ensure_cache_dir(), _METADATA_FILE)


def _load_cache_index() -> dict:
    """Load cached image metadata from disk."""
    path = _get_metadata_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": 1, "images": {}, "queries": {}}


def _save_cache_index(index: dict) -> None:
    """Persist cached image metadata to disk."""
    path = _get_metadata_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Image Provider] Warning: Failed to save cache index: {e}")


def validate_image_file(file_path: str) -> bool:
    """
    Validate that a file exists, is non-empty, and contains a valid image byte signature.
    Also verifies with PIL if installed.
    """
    if not file_path or not os.path.isfile(file_path):
        return False

    try:
        size = os.path.getsize(file_path)
        if size < 256:  # Real photographs are well over 256 bytes
            return False

        with open(file_path, "rb") as f:
            header = f.read(32)

        is_valid_magic = False
        if header.startswith(b"\xff\xd8\xff"):
            is_valid_magic = True
        elif header.startswith(b"\x89PNG\r\n\x1a\n"):
            is_valid_magic = True
        elif header.startswith(b"RIFF") and b"WEBP" in header[:16]:
            is_valid_magic = True
        elif header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
            is_valid_magic = True

        if not is_valid_magic:
            return False

        # Optional PIL validation if available
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                img.verify()
        except ImportError:
            pass
        except Exception:
            return False

        return True
    except Exception:
        return False


def _slugify(text: str, max_length: int = 40) -> str:
    """Create a safe filename slug from arbitrary text."""
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:max_length] or "image"


def _clean_query(query: str) -> str:
    """Normalize search query string."""
    q = re.sub(r"[^\w\s-]", " ", query).strip()
    return re.sub(r"\s+", " ", q).strip()


def get_cached_image(query: str) -> Optional[Dict[str, Any]]:
    """Return cached image metadata if a valid local image exists for the query."""
    q = _clean_query(query).lower()
    if not q:
        return None

    index = _load_cache_index()
    img_id = index.get("queries", {}).get(q)
    if img_id and img_id in index.get("images", {}):
        meta = index["images"][img_id]
        local_path = meta.get("local_path", "")
        if validate_image_file(local_path):
            return meta

    # Fallback: check query hash
    q_hash = hashlib.md5(q.encode("utf-8")).hexdigest()[:12]
    for key, meta in index.get("images", {}).items():
        if meta.get("query_hash") == q_hash:
            local_path = meta.get("local_path", "")
            if validate_image_file(local_path):
                return meta

    return None


def _check_local_asset(query: str) -> Optional[Dict[str, Any]]:
    """
    Check if an existing approved company asset matches the query.
    Local company assets take highest priority per Section 9.
    """
    try:
        from tools.asset_manager import get_best_image
        local_path = get_best_image(query)
        if local_path and validate_image_file(local_path):
            norm_path = os.path.abspath(local_path).replace("\\", "/")
            fname = os.path.basename(local_path)
            alt_text = os.path.splitext(fname)[0].replace("_", " ").replace("-", " ")
            print(f"[Image Provider] Found matching company asset: {fname} for query '{query}'")
            return {
                "local_path": norm_path,
                "relative_path": os.path.relpath(local_path, ASSETS_DIR).replace("\\", "/"),
                "source": "local_asset",
                "source_url": "",
                "photographer": "Company Asset Library",
                "photographer_url": "",
                "query": query,
                "image_id": f"local_{_slugify(fname)}",
                "alt_text": alt_text,
                "width": 1920,
                "height": 1080,
            }
    except Exception as e:
        print(f"[Image Provider] Local asset lookup check failed: {e}")
    return None


def _download_pexels_image(photo: dict, query: str) -> Optional[Dict[str, Any]]:
    """
    Download photograph selected from Pexels and persist to cache directory.
    Validates file integrity before registering in cache.
    """
    photo_id = str(photo.get("id", ""))
    if not photo_id:
        return None

    src = photo.get("src", {})
    # Select best resolution: prefer large (1080p web-ready) or medium
    # Avoid large2x (4K/Retina) which causes huge multi-megabyte presentations and stalls email uploads
    img_url = (
        src.get("large")
        or src.get("medium")
        or src.get("landscape")
        or src.get("large2x")
        or src.get("original")
    )
    if not img_url:
        return None

    cache_dir = _ensure_cache_dir()
    slug = _slugify(photo.get("alt") or query, max_length=30)
    filename = f"pexels_{photo_id}_{slug}.jpg"
    local_path = os.path.join(cache_dir, filename)
    norm_local_path = os.path.abspath(local_path).replace("\\", "/")

    # Check if already downloaded and valid
    if validate_image_file(local_path):
        meta = {
            "local_path": norm_local_path,
            "relative_path": os.path.relpath(local_path, os.path.dirname(ASSETS_IMAGES_CACHE_DIR)).replace("\\", "/"),
            "source": "pexels",
            "source_url": photo.get("url", ""),
            "photographer": photo.get("photographer", "Pexels Contributor"),
            "photographer_url": photo.get("photographer_url", ""),
            "query": query,
            "query_hash": hashlib.md5(_clean_query(query).lower().encode("utf-8")).hexdigest()[:12],
            "image_id": photo_id,
            "alt_text": photo.get("alt") or query,
            "width": photo.get("width", 1920),
            "height": photo.get("height", 1080),
            "cached_at": datetime.now().isoformat(timespec="seconds"),
        }
        return meta

    # Download image bytes
    headers = {
        "User-Agent": "PhantomOps-Sally/1.0 (Presentation Engine; +https://eaisystems.com)"
    }
    req = urllib.request.Request(img_url, headers=headers)

    temp_path = local_path + ".tmp"
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SEC) as response:
            if response.status != 200:
                print(f"[Image Provider] Download failed HTTP {response.status} for photo {photo_id}")
                return None
            data = response.read()

        with open(temp_path, "wb") as f:
            f.write(data)

        # Validate downloaded content
        if not validate_image_file(temp_path):
            print(f"[Image Provider] Downloaded file corrupted or invalid magic bytes: {photo_id}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return None

        # Atomically replace destination
        if os.path.exists(local_path):
            os.remove(local_path)
        os.rename(temp_path, local_path)

        # Optimize image with Pillow to keep presentation file sizes under 2MB
        try:
            from PIL import Image
            with Image.open(local_path) as im:
                if im.mode in ("RGBA", "P"):
                    im = im.convert("RGB")
                if im.width > 1600 or im.height > 1200:
                    im.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
                im.save(local_path, "JPEG", quality=82, optimize=True)
        except Exception:
            pass

        meta = {
            "local_path": norm_local_path,
            "relative_path": os.path.relpath(local_path, os.path.dirname(ASSETS_IMAGES_CACHE_DIR)).replace("\\", "/"),
            "source": "pexels",
            "source_url": photo.get("url", ""),
            "photographer": photo.get("photographer", "Pexels Contributor"),
            "photographer_url": photo.get("photographer_url", ""),
            "query": query,
            "query_hash": hashlib.md5(_clean_query(query).lower().encode("utf-8")).hexdigest()[:12],
            "image_id": photo_id,
            "alt_text": photo.get("alt") or query,
            "width": photo.get("width", 1920),
            "height": photo.get("height", 1080),
            "cached_at": datetime.now().isoformat(timespec="seconds"),
        }

        # Update cache index
        index = _load_cache_index()
        index.setdefault("images", {})[photo_id] = meta
        index.setdefault("queries", {})[_clean_query(query).lower()] = photo_id
        _save_cache_index(index)

        print(f"[Image Provider] Successfully cached Pexels photo: {filename} ({len(data)} bytes)")
        return meta

    except Exception as e:
        print(f"[Image Provider] Error downloading image from Pexels ({img_url}): {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return None


def fetch_relevant_image(
    query: str,
    orientation: str = "landscape",
) -> Optional[Dict[str, Any]]:
    """
    Return a relevant real photograph with its local path and source metadata.

    Resolution order:
      1. Check existing local company assets (via tools.asset_manager)
      2. Check local disk cache (prevent redundant network requests)
      3. Query Pexels API with slide-specific query
      4. Download, validate byte integrity, cache, and return metadata

    Returns:
      dict with keys: local_path, relative_path, source, source_url,
                      photographer, photographer_url, query, image_id, alt_text
      or None if retrieval fails / missing API key / no results.
    """
    clean_q = _clean_query(query)
    if not clean_q:
        return None

    # 1. Check local company asset library first
    local_asset = _check_local_asset(clean_q)
    if local_asset:
        return local_asset

    # 2. Check local disk cache
    cached = get_cached_image(clean_q)
    if cached:
        print(f"[Image Provider] Reusing cached image for '{clean_q}': {os.path.basename(cached['local_path'])}")
        return cached

    # 3. Read Pexels API Key
    api_key = os.environ.get("PEXELS_API_KEY") if "PEXELS_API_KEY" in os.environ else PEXELS_API_KEY
    if not api_key or not api_key.strip():
        print(f"[Image Provider] PEXELS_API_KEY not configured — skipping external image search for '{clean_q}'")
        return None

    # 4. Search Pexels API
    params = urllib.parse.urlencode({
        "query": clean_q,
        "orientation": orientation,
        "per_page": 5,
    })
    url = f"{PEXELS_SEARCH_URL}?{params}"
    headers = {
        "Authorization": api_key.strip(),
        "User-Agent": "PhantomOps-Sally/1.0",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SEC) as resp:
            if resp.status != 200:
                print(f"[Image Provider] Pexels API returned status {resp.status}")
                return None
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.HTTPError as he:
        if he.code == 401:
            print("[Image Provider] Pexels API authentication failed (invalid or expired PEXELS_API_KEY)")
        elif he.code == 429:
            print("[Image Provider] Pexels API rate limit reached")
        else:
            print(f"[Image Provider] Pexels HTTP Error {he.code}: {he.reason}")
        return None
    except urllib.error.URLError as ue:
        print(f"[Image Provider] Pexels connection error: {ue.reason}")
        return None
    except Exception as e:
        print(f"[Image Provider] Pexels search failed: {e}")
        return None

    photos = data.get("photos", [])
    if not photos:
        print(f"[Image Provider] No Pexels photographs found for query '{clean_q}'")
        return None

    # Pick the best matching photo (first result from relevance search)
    selected_photo = photos[0]
    return _download_pexels_image(selected_photo, clean_q)


def clear_cache() -> None:
    """Clear cached images and metadata index (useful for tests and cleanup)."""
    cache_dir = _ensure_cache_dir()
    for fname in os.listdir(cache_dir):
        fpath = os.path.join(cache_dir, fname)
        if os.path.isfile(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass
