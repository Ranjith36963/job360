"""Disk-based LLM response cache using content hashing.

Stores responses as individual JSON files in ``data/llm_cache/``, keyed by
SHA-256 of (prompt_type + content).  Avoids redundant API calls for the same
JD across runs.  Cache has no TTL — JDs don't change, and the user can delete
the directory to clear it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from src.config.settings import LLM_CACHE_DIR

logger = logging.getLogger("job360.llm.cache")

_dir_created = False
_stats: dict[str, int] = {"hits": 0, "misses": 0}


def _cache_dir() -> Path:
    """Return (and lazily create) the LLM cache directory."""
    global _dir_created
    if not _dir_created:
        LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _dir_created = True
    return LLM_CACHE_DIR


def cache_key(prompt_type: str, content: str) -> str:
    """Generate a 16-hex-char SHA-256 key from *prompt_type* + *content*."""
    blob = f"{prompt_type}:{content}".encode("utf-8", errors="replace")
    return hashlib.sha256(blob).hexdigest()[:16]


def get_cached(key: str) -> Optional[dict]:
    """Retrieve a cached LLM response, or ``None`` if not cached."""
    path = _cache_dir() / f"{key}.json"
    if not path.exists():
        _stats["misses"] += 1
        return None
    try:
        _data = json.loads(path.read_text(encoding="utf-8")).get("data")
        _stats["hits"] += 1
        return _data
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug(f"Cache read error for {key}: {exc}")
        return None


def set_cached(key: str, data: dict) -> None:
    """Store an LLM response in the cache (atomic write)."""
    path = _cache_dir() / f"{key}.json"
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps({"data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)  # atomic on most OSes
    except OSError as exc:
        logger.debug(f"Cache write error for {key}: {exc}")
        tmp.unlink(missing_ok=True)


def cache_stats() -> dict[str, int]:
    """Return cache hit/miss statistics for the current session."""
    return dict(_stats)


def reset_stats() -> None:
    """Reset cache statistics (useful for testing)."""
    _stats["hits"] = 0
    _stats["misses"] = 0
