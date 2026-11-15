"""Staged SEC filing fetch and validated cache promotion."""

from __future__ import annotations

import hashlib
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from insider_scanner.core import edgar
from insider_scanner.core._sec_paths import resolves_within
from insider_scanner.core.sec_client import SecClient
from insider_scanner.core.sec_index import SecMasterIndexRow, filing_accession
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceProfile,
    SecSecurityPolicy,
    SecSecurityReason,
)
from insider_scanner.utils.atomic_file import atomic_write_bytes
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_downloader")

_CACHE_NAMESPACE = "validated-v1"
_CACHE_SUFFIX = ".filing"
_DIAGNOSTICS_NAMESPACE = "diagnostics"
_DIAGNOSTICS_SUFFIX = ".raw"
_MAX_FILING_BYTES = DEFAULT_SEC_SECURITY_POLICY.limits_for(
    SecResourceProfile.FILING_DOCUMENT
).max_bytes


@dataclass(frozen=True, slots=True)
class PendingSecFiling:
    """Bounded filing bytes that have not necessarily passed domain parsing."""

    row: SecMasterIndexRow
    archive_url: str
    content: bytes
    content_path: Path
    from_cache: bool
    max_bytes: int


@dataclass(frozen=True, slots=True)
class DownloadedSecFiling:
    """Parser-approved SEC filing and its local cache provenance."""

    row: SecMasterIndexRow
    archive_url: str
    content_path: Path
    from_cache: bool


class SecDownloadError(Exception):
    """Base class for safe SEC download/cache failures."""


class SecDownloadSecurityError(SecDownloadError):
    """Raised when a cache operation violates the path policy."""

    def __init__(
        self, reason: SecSecurityReason = SecSecurityReason.CACHE_PATH
    ) -> None:
        self.reason = reason
        super().__init__(f"SEC cache operation rejected ({reason.value})")


class SecDownloadIoError(SecDownloadError):
    """Raised when a cache filesystem operation fails."""

    def __init__(self) -> None:
        super().__init__("SEC cache filesystem operation failed")


def fetch_filing(
    row: SecMasterIndexRow,
    *,
    client: SecClient,
    cache_root: Path,
    cache: bool = True,
) -> PendingSecFiling:
    """Return trusted cache bytes or bounded network bytes without writing them.

    ``cache=False`` bypasses all cache filesystem work for durable bulk ingestion.
    """
    _validate_inputs(row, client, cache_root, cache=cache)
    archive_url = edgar.build_filing_archive_url(row.archive_path)
    content_path = _content_path(row, cache_root)
    limits = client.policy.limits_for(SecResourceProfile.FILING_DOCUMENT)
    if cache:
        _validate_content_path(content_path, cache_root)
        cached = _read_fresh_cache(
            content_path,
            client.policy.cache_freshness_seconds,
            limits.max_bytes,
        )
        if cached is None:
            # Existing installations used the entire CIK-specific archive path.
            # Reuse those bytes, retaining the original user cache file.
            legacy_path = _legacy_content_path(row, cache_root)
            if legacy_path != content_path:
                _validate_content_path(legacy_path, cache_root)
                cached = _read_fresh_cache(
                    legacy_path,
                    client.policy.cache_freshness_seconds,
                    limits.max_bytes,
                )
                if cached is not None:
                    return PendingSecFiling(
                        row, archive_url, cached, legacy_path, True, limits.max_bytes
                    )
        if cached is not None:
            return PendingSecFiling(
                row, archive_url, cached, content_path, True, limits.max_bytes
            )
    content = client.fetch_bytes(
        archive_url, profile=SecResourceProfile.FILING_DOCUMENT
    )
    return PendingSecFiling(
        row, archive_url, content, content_path, False, limits.max_bytes
    )


def promote_validated_filing(
    pending: PendingSecFiling,
    *,
    cache_root: Path,
) -> DownloadedSecFiling:
    """Atomically persist bytes after the caller has completed domain parsing."""
    if type(pending) is not PendingSecFiling:
        raise TypeError("pending must be exactly PendingSecFiling")
    if type(pending.row) is not SecMasterIndexRow:
        raise TypeError("pending.row must be exactly SecMasterIndexRow")
    if not isinstance(cache_root, Path):
        raise TypeError("cache_root must be a pathlib.Path")
    _validate_cache_root(cache_root)
    expected_url = edgar.build_filing_archive_url(pending.row.archive_path)
    expected_path = _content_path(pending.row, cache_root)
    _validate_content_path(expected_path, cache_root)
    allowed_paths = {expected_path}
    if pending.from_cache:
        allowed_paths.add(_legacy_content_path(pending.row, cache_root))
    if pending.archive_url != expected_url or pending.content_path not in allowed_paths:
        raise SecDownloadSecurityError()
    if pending.from_cache:
        if pending.content_path != expected_path:
            _validate_content_path(pending.content_path, cache_root)
        _require_regular_file(pending.content_path)
        if pending.content_path == expected_path:
            return DownloadedSecFiling(
                pending.row, pending.archive_url, expected_path, True
            )
    if (
        isinstance(pending.max_bytes, bool)
        or not isinstance(pending.max_bytes, int)
        or not 0 < pending.max_bytes <= _MAX_FILING_BYTES
    ):
        raise SecDownloadSecurityError(SecSecurityReason.RESPONSE_SIZE)
    if type(pending.content) is not bytes or len(pending.content) > pending.max_bytes:
        raise SecDownloadSecurityError(SecSecurityReason.RESPONSE_SIZE)
    try:
        _prepare_cache_directory(expected_path.parent, cache_root)
        _write_atomically(expected_path, pending.content)
    except SecDownloadError:
        raise
    except OSError as exc:
        raise SecDownloadIoError() from exc
    return DownloadedSecFiling(
        pending.row, pending.archive_url, expected_path, pending.from_cache
    )


def purge_stale_cache(
    cache_root: Path,
    *,
    max_age_seconds: float | None = None,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
) -> int:
    """Remove validated-cache files older than *max_age_seconds* and return count.

    The age threshold defaults to ``policy.cache_freshness_seconds``. Only
    regular files inside the ``validated-v1`` namespace are considered; symlinks
    are never followed or removed, and a symlinked namespace is rejected.
    """
    if not isinstance(cache_root, Path):
        raise TypeError("cache_root must be a pathlib.Path")
    try:
        _validate_cache_root(cache_root)
    except SecDownloadIoError:
        _log.warning("SEC cache cleanup could not inspect the cache root")
        return 0
    threshold = (
        policy.cache_freshness_seconds if max_age_seconds is None else max_age_seconds
    )
    if threshold < 0:
        raise ValueError("max_age_seconds must not be negative")
    namespace = cache_root / _CACHE_NAMESPACE
    try:
        if _is_link(namespace):
            raise SecDownloadSecurityError()
        if not namespace.is_dir():
            return 0
    except OSError:
        _log.warning("SEC cache cleanup could not inspect the namespace")
        return 0

    now = time.time()
    removed = 0
    try:
        with os.scandir(namespace) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    metadata = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(metadata.st_mode):
                        continue
                    if now - metadata.st_mtime <= threshold:
                        continue
                    os.unlink(entry.path)
                except OSError:
                    # A locked or vanished entry must not abandon other files.
                    continue
                removed += 1
    except OSError:
        _log.warning("SEC cache cleanup could not enumerate the namespace")
    return removed


def quarantine_failed_download(
    pending: PendingSecFiling,
    *,
    cache_root: Path,
) -> Path:
    """Persist raw bytes of a failed filing under ``diagnostics`` for inspection.

    Intended for opt-in debug/diagnostic mode only. Reuses the same path-safety
    guards and atomic write as the validated cache so a hostile archive path can
    never escape *cache_root*.
    """
    if type(pending) is not PendingSecFiling:
        raise TypeError("pending must be exactly PendingSecFiling")
    if type(pending.row) is not SecMasterIndexRow:
        raise TypeError("pending.row must be exactly SecMasterIndexRow")
    if not isinstance(cache_root, Path):
        raise TypeError("cache_root must be a pathlib.Path")
    if type(pending.content) is not bytes:
        raise SecDownloadSecurityError(SecSecurityReason.RESPONSE_SIZE)
    _validate_cache_root(cache_root)
    digest = hashlib.sha256(pending.row.archive_path.encode("utf-8")).hexdigest()
    diagnostics_dir = cache_root / _DIAGNOSTICS_NAMESPACE
    target = diagnostics_dir / f"{digest}{_DIAGNOSTICS_SUFFIX}"
    try:
        if not resolves_within(target, cache_root):
            raise SecDownloadSecurityError()
        if diagnostics_dir.is_symlink() or target.is_symlink():
            raise SecDownloadSecurityError()
        _prepare_cache_directory(diagnostics_dir, cache_root)
        _write_atomically(target, pending.content)
    except SecDownloadError:
        raise
    except OSError as exc:
        raise SecDownloadIoError() from exc
    return target


def _validate_inputs(
    row: SecMasterIndexRow, client: SecClient, cache_root: Path, *, cache: bool
) -> None:
    if type(row) is not SecMasterIndexRow:
        raise TypeError("row must be exactly SecMasterIndexRow")
    if type(client) is not SecClient:
        raise TypeError("client must be exactly SecClient")
    if not isinstance(cache_root, Path):
        raise TypeError("cache_root must be a pathlib.Path")
    if cache:
        _validate_cache_root(cache_root)


def _validate_cache_root(cache_root: Path) -> None:
    try:
        if cache_root.is_symlink():
            raise SecDownloadSecurityError()
        if cache_root.exists() and not cache_root.is_dir():
            raise SecDownloadSecurityError()
    except OSError as exc:
        raise SecDownloadIoError() from exc


def _content_path(row: SecMasterIndexRow, cache_root: Path) -> Path:
    digest = hashlib.sha256(
        filing_accession(row.archive_path).encode("ascii")
    ).hexdigest()
    return cache_root / _CACHE_NAMESPACE / f"{digest}{_CACHE_SUFFIX}"


def _legacy_content_path(row: SecMasterIndexRow, cache_root: Path) -> Path:
    digest = hashlib.sha256(row.archive_path.encode("utf-8")).hexdigest()
    return cache_root / _CACHE_NAMESPACE / f"{digest}{_CACHE_SUFFIX}"


def _validate_content_path(content_path: Path, cache_root: Path) -> None:
    try:
        namespace = cache_root / _CACHE_NAMESPACE
        # The filename is a SHA-256 digest produced here. A lexical direct-child
        # check plus link guards needs no per-filing root/path resolution.
        if content_path.parent != namespace:
            raise SecDownloadSecurityError()
        if _is_link(namespace) or content_path.is_symlink():
            raise SecDownloadSecurityError()
    except OSError as exc:
        raise SecDownloadIoError() from exc


def _is_link(path: Path) -> bool:
    """Reject symlinks and Windows junction/reparse directories alike."""
    if path.is_symlink():
        return True
    if os.name == "nt":
        try:
            attributes = path.lstat().st_file_attributes
        except FileNotFoundError:
            return False
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def _read_fresh_cache(
    content_path: Path, freshness_seconds: float, max_bytes: int
) -> bytes | None:
    try:
        try:
            metadata = content_path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode):
            raise SecDownloadSecurityError()
        if metadata.st_size > max_bytes:
            try:
                content_path.unlink()
            except OSError:
                pass
            raise SecDownloadSecurityError(SecSecurityReason.RESPONSE_SIZE)
        if time.time() - metadata.st_mtime > freshness_seconds:
            content_path.unlink()
            return None
        return content_path.read_bytes()
    except OSError as exc:
        raise SecDownloadIoError() from exc


def _require_regular_file(content_path: Path) -> None:
    try:
        metadata = content_path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise SecDownloadSecurityError() from exc
    except OSError as exc:
        raise SecDownloadIoError() from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SecDownloadSecurityError()


def _prepare_cache_directory(directory: Path, cache_root: Path) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    _validate_cache_root(cache_root)
    directory.mkdir(parents=False, exist_ok=True)
    if _is_link(directory) or not directory.is_dir():
        raise SecDownloadSecurityError()


def _write_atomically(content_path: Path, content: bytes) -> None:
    atomic_write_bytes(content_path, content)
