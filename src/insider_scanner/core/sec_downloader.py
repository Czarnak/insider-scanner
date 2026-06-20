"""Staged SEC filing fetch and validated cache promotion."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time

from insider_scanner.core import edgar
from insider_scanner.core.sec_client import SecClient
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceProfile,
    SecSecurityReason,
)


_CACHE_NAMESPACE = "validated-v1"
_CACHE_SUFFIX = ".filing"
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

    def __init__(self, reason: SecSecurityReason = SecSecurityReason.CACHE_PATH) -> None:
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
) -> PendingSecFiling:
    """Return trusted cache bytes or bounded network bytes without writing them."""
    _validate_inputs(row, client, cache_root)
    archive_url = edgar.build_filing_archive_url(row.archive_path)
    content_path = _content_path(row, cache_root)
    _validate_content_path(content_path, cache_root)
    limits = client.policy.limits_for(SecResourceProfile.FILING_DOCUMENT)
    cached = _read_fresh_cache(
        content_path,
        client.policy.cache_freshness_seconds,
        limits.max_bytes,
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
    if pending.archive_url != expected_url or pending.content_path != expected_path:
        raise SecDownloadSecurityError()
    if pending.from_cache:
        _require_regular_file(expected_path)
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
    return DownloadedSecFiling(pending.row, pending.archive_url, expected_path, False)


def _validate_inputs(
    row: SecMasterIndexRow, client: SecClient, cache_root: Path
) -> None:
    if type(row) is not SecMasterIndexRow:
        raise TypeError("row must be exactly SecMasterIndexRow")
    if type(client) is not SecClient:
        raise TypeError("client must be exactly SecClient")
    if not isinstance(cache_root, Path):
        raise TypeError("cache_root must be a pathlib.Path")
    _validate_cache_root(cache_root)


def _validate_cache_root(cache_root: Path) -> None:
    if cache_root.is_symlink():
        raise SecDownloadSecurityError()
    if cache_root.exists() and not cache_root.is_dir():
        raise SecDownloadSecurityError()


def _content_path(row: SecMasterIndexRow, cache_root: Path) -> Path:
    digest = hashlib.sha256(row.archive_path.encode("utf-8")).hexdigest()
    return cache_root / _CACHE_NAMESPACE / f"{digest}{_CACHE_SUFFIX}"


def _validate_content_path(content_path: Path, cache_root: Path) -> None:
    resolved_root = cache_root.resolve(strict=False)
    resolved_path = content_path.resolve(strict=False)
    if not resolved_path.is_relative_to(resolved_root):
        raise SecDownloadSecurityError()
    namespace = cache_root / _CACHE_NAMESPACE
    if namespace.is_symlink() or content_path.is_symlink():
        raise SecDownloadSecurityError()


def _read_fresh_cache(
    content_path: Path, freshness_seconds: float, max_bytes: int
) -> bytes | None:
    try:
        metadata = content_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise SecDownloadSecurityError()
    if metadata.st_size > max_bytes:
        content_path.unlink()
        raise SecDownloadSecurityError(SecSecurityReason.RESPONSE_SIZE)
    if time.time() - metadata.st_mtime > freshness_seconds:
        content_path.unlink()
        return None
    return content_path.read_bytes()


def _require_regular_file(content_path: Path) -> None:
    try:
        metadata = content_path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise SecDownloadSecurityError() from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SecDownloadSecurityError()


def _prepare_cache_directory(directory: Path, cache_root: Path) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    _validate_cache_root(cache_root)
    directory.mkdir(parents=False, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise SecDownloadSecurityError()


def _write_atomically(content_path: Path, content: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".sec-filing-", suffix=".tmp", dir=content_path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.replace(content_path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise
