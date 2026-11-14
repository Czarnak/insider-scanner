"""Best-effort HTTP response caching with atomic, single-file entries."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from insider_scanner.utils.atomic_file import atomic_write_bytes
from insider_scanner.utils.config import DEFAULT_CACHE_TTL
from insider_scanner.utils.logging import get_logger

log = get_logger("caching")

_CACHE_SUFFIX = ".response-cache"
_CACHE_VERSION = 1
_LEGACY_SUFFIXES = frozenset({".txt", ".meta"})


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    timestamp: float
    ttl: float
    content: str


def cache_key(url: str) -> str:
    """Create a filesystem-safe cache key from a URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def get_cached(cache_dir: Path, key: str, ttl: int = DEFAULT_CACHE_TTL) -> str | None:
    """Return an intact, unexpired response, or a miss on any cache failure.

    Legacy ``.txt``/``.meta`` pairs are deliberately ignored: a fresh timestamp
    cannot establish whether their separately written body was truncated.
    """
    entry = _read_entry(cache_dir / f"{key}{_CACHE_SUFFIX}")
    if entry is None:
        return None
    if time.time() - entry.timestamp > ttl:
        log.debug("Cache expired for %s", key)
        return None
    return entry.content


def set_cached(
    cache_dir: Path, key: str, content: str, ttl: int = DEFAULT_CACHE_TTL
) -> None:
    """Atomically publish timestamp and body; a failed cache write is nonfatal."""
    path = cache_dir / f"{key}{_CACHE_SUFFIX}"
    lifetime = _finite_nonnegative_number(ttl)
    if lifetime is None:
        log.warning("Could not cache response %s: invalid lifetime", key)
        return
    try:
        payload = json.dumps(
            {
                "version": _CACHE_VERSION,
                "timestamp": time.time(),
                "ttl": lifetime,
                "content": content,
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        atomic_write_bytes(path, payload)
    except (OSError, UnicodeError) as exc:
        log.warning("Could not cache response %s: %s", key, exc)
        return
    log.debug("Cached %d chars for %s", len(content), key)


def _read_entry(path: Path) -> _CacheEntry | None:
    """Validate a disposable cache envelope without preserving corrupt files."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        log.debug("Could not read response cache %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    timestamp = _finite_nonnegative_number(data.get("timestamp"))
    lifetime = _finite_nonnegative_number(data.get("ttl"))
    content = data.get("content")
    if (
        type(version) is not int
        or version != _CACHE_VERSION
        or timestamp is None
        or lifetime is None
        or not isinstance(content, str)
    ):
        return None
    return _CacheEntry(timestamp, lifetime, content)


def _finite_nonnegative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if number >= 0 and math.isfinite(number) else None


def _cache_files(cache_dir: Path) -> Iterator[Path]:
    """Enumerate only cache-owned formats; directory failures are nonfatal."""
    try:
        for path in cache_dir.iterdir():
            if path.suffix != _CACHE_SUFFIX and path.suffix not in _LEGACY_SUFFIXES:
                continue
            try:
                if path.is_file() and not path.is_symlink():
                    yield path
            except OSError as exc:
                log.debug("Could not inspect response cache %s: %s", path, exc)
    except OSError as exc:
        log.debug("Could not enumerate response cache %s: %s", cache_dir, exc)


def _remove_entry(path: Path) -> bool:
    try:
        path.unlink()
    except OSError as exc:
        log.debug("Could not remove response cache %s: %s", path, exc)
        return False
    return True


def prune_expired_cache(cache_dir: Path, ttl: int | None = None) -> int:
    """Remove expired, malformed and legacy entries, including unrequested keys.

    Call once during startup rather than scanning the directory per request.
    Locked files and inaccessible directories are left for a later sweep.
    Entries retain the lifetime used by their HTTP request, unless *ttl*
    explicitly overrides it. A stored lifetime of zero is immediately stale.
    """
    removed = 0
    now = time.time()
    for path in _cache_files(cache_dir):
        entry = _read_entry(path) if path.suffix == _CACHE_SUFFIX else None
        if entry is None or now - entry.timestamp >= (
            entry.ttl if ttl is None else ttl
        ):
            removed += _remove_entry(path)
    return removed


def clear_cache(cache_dir: Path) -> int:
    """Best-effort removal of new and legacy cache files; return removed count."""
    return sum(_remove_entry(path) for path in _cache_files(cache_dir))
