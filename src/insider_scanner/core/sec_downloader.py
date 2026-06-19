"""Cache-aware downloader for SEC filing archive documents."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import time

from insider_scanner.core import edgar
from insider_scanner.core.sec_client import SecClient
from insider_scanner.core.sec_index import SecMasterIndexRow


@dataclass(frozen=True, slots=True)
class DownloadedSecFiling:
    """A downloaded SEC filing and its local cache provenance."""

    row: SecMasterIndexRow
    archive_url: str
    content_path: Path
    from_cache: bool


def download_filing(
    row: SecMasterIndexRow,
    *,
    client: SecClient,
    cache_dir: Path,
    max_age_seconds: float | None = None,
) -> DownloadedSecFiling:
    """Download one SEC filing to a deterministic local cache path."""
    if type(row) is not SecMasterIndexRow:
        raise TypeError("row must be exactly SecMasterIndexRow")
    if type(client) is not SecClient:
        raise TypeError("client must be exactly SecClient")
    if not isinstance(cache_dir, Path):
        raise TypeError("cache_dir must be a pathlib.Path")
    if max_age_seconds is not None and (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
    ):
        raise TypeError("max_age_seconds must be None or a finite positive number")
    if max_age_seconds is not None and (
        not math.isfinite(max_age_seconds) or max_age_seconds <= 0
    ):
        raise ValueError("max_age_seconds must be finite and greater than zero")
    archive_url = edgar.build_filing_archive_url(row.archive_path)
    content_path = _content_path(row, cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if max_age_seconds is not None and _is_fresh_regular_file(
        content_path, max_age_seconds
    ):
        return DownloadedSecFiling(row, archive_url, content_path, True)
    content = client.fetch_bytes(archive_url)
    _write_atomically(content_path, content)
    return DownloadedSecFiling(row, archive_url, content_path, False)


def _content_path(row: SecMasterIndexRow, cache_dir: Path) -> Path:
    digest = hashlib.sha256(row.archive_path.encode("utf-8")).hexdigest()
    suffix = PurePosixPath(row.archive_path).suffix.lower()
    return cache_dir / f"{digest}{suffix}"


def _is_fresh_regular_file(content_path: Path, max_age_seconds: float) -> bool:
    try:
        metadata = content_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    age_seconds = time.time() - metadata.st_mtime
    return stat.S_ISREG(metadata.st_mode) and age_seconds <= max_age_seconds


def _write_atomically(content_path: Path, content: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".sec-filing-", suffix=".tmp", dir=content_path.parent
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        temp_path.write_bytes(content)
        temp_path.replace(content_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
