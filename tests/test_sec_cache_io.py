"""Locked SEC cache entries remain recoverable without weakening path policy."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from insider_scanner.core import sec_downloader
from insider_scanner.core.sec_index import parse_master_index
from insider_scanner.core.sec_security import SecSecurityReason
from insider_scanner.services.sec_downloads import download_filings_in_range
from tests.sec_stubs import (
    DAY,
    INDEX_TWO_ROWS,
    VALID_FILING,
    make_client,
    make_transport,
)


@pytest.mark.parametrize("operation", ["stat", "read_bytes", "unlink", "resolve"])
def test_cache_filesystem_failures_are_typed(tmp_path, monkeypatch, operation):
    row = parse_master_index(INDEX_TWO_ROWS)[0]
    transport = make_transport()
    client = make_client(transport)
    pending = sec_downloader.fetch_filing(row, client=client, cache_root=tmp_path)
    path = pending.content_path
    path.parent.mkdir()
    path.write_bytes(VALID_FILING)
    if operation == "unlink":
        os.utime(path, (0, 0))
    original = getattr(Path, operation)

    def locked(self, *args, **kwargs):
        if self == path:
            raise PermissionError("cache entry locked")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, operation, locked)
    with pytest.raises(sec_downloader.SecDownloadIoError) as exc_info:
        sec_downloader.fetch_filing(row, client=client, cache_root=tmp_path)
    assert isinstance(exc_info.value.__cause__, PermissionError)
    assert "locked" not in str(exc_info.value)
    assert transport.get.call_count == 1


def test_oversized_locked_entry_keeps_size_security_reason(tmp_path, monkeypatch):
    path = tmp_path / "oversized.filing"
    path.write_bytes(b"too large")

    def locked(*args, **kwargs):
        raise PermissionError("cannot unlink")

    monkeypatch.setattr(Path, "unlink", locked)
    with pytest.raises(sec_downloader.SecDownloadSecurityError) as exc_info:
        sec_downloader._read_fresh_cache(path, freshness_seconds=60, max_bytes=1)
    assert exc_info.value.reason is SecSecurityReason.RESPONSE_SIZE
    assert path.read_bytes() == b"too large"


def test_cache_hit_promotion_stat_failure_is_typed(tmp_path, monkeypatch):
    row = parse_master_index(INDEX_TWO_ROWS)[0]
    client = make_client(make_transport())
    pending = sec_downloader.fetch_filing(row, client=client, cache_root=tmp_path)
    sec_downloader.promote_validated_filing(pending, cache_root=tmp_path)
    cached = sec_downloader.fetch_filing(row, client=client, cache_root=tmp_path)
    original = Path.stat

    def locked(self, *args, **kwargs):
        if self == cached.content_path:
            raise PermissionError("cache entry locked")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", locked)
    with pytest.raises(sec_downloader.SecDownloadIoError):
        sec_downloader.promote_validated_filing(cached, cache_root=tmp_path)


@pytest.mark.parametrize("operation", ["stat", "unlink", "resolve"])
def test_purge_skips_locked_entry_and_removes_other_stale_files(
    tmp_path, monkeypatch, operation
):
    namespace = tmp_path / "validated-v1"
    namespace.mkdir()
    locked_path = namespace / "locked.filing"
    removable = namespace / "other.filing"
    for path in (locked_path, removable):
        path.write_bytes(b"stale")
        os.utime(path, (0, 0))
    original = getattr(Path, operation)

    def locked(self, *args, **kwargs):
        if self == locked_path:
            raise PermissionError("cache entry locked")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, operation, locked)
    assert sec_downloader.purge_stale_cache(tmp_path, max_age_seconds=1) == 1
    monkeypatch.undo()
    assert locked_path.read_bytes() == b"stale"
    assert not removable.exists()


def test_purge_namespace_enumeration_failure_is_nonfatal(tmp_path, monkeypatch):
    namespace = tmp_path / "validated-v1"
    namespace.mkdir()
    original = Path.iterdir

    def locked(self):
        if self == namespace:
            raise PermissionError("directory locked")
        return original(self)

    monkeypatch.setattr(Path, "iterdir", locked)
    assert sec_downloader.purge_stale_cache(tmp_path, max_age_seconds=1) == 0


def test_purge_root_inspection_failure_is_nonfatal(tmp_path, monkeypatch):
    original = Path.is_symlink

    def locked(self):
        if self == tmp_path:
            raise PermissionError("root locked")
        return original(self)

    monkeypatch.setattr(Path, "is_symlink", locked)
    assert sec_downloader.purge_stale_cache(tmp_path, max_age_seconds=1) == 0


def test_purge_keeps_explicit_namespace_symlink_rejection(tmp_path, monkeypatch):
    namespace = tmp_path / "validated-v1"
    original = Path.is_symlink

    def symlinked(self):
        return self == namespace or original(self)

    monkeypatch.setattr(Path, "is_symlink", symlinked)
    with pytest.raises(sec_downloader.SecDownloadSecurityError) as exc_info:
        sec_downloader.purge_stale_cache(tmp_path)
    assert exc_info.value.reason is SecSecurityReason.CACHE_PATH


def test_quarantine_path_resolution_failure_is_typed(tmp_path, monkeypatch):
    row = parse_master_index(INDEX_TWO_ROWS)[0]
    client = make_client(make_transport())
    pending = sec_downloader.fetch_filing(row, client=client, cache_root=tmp_path)

    def locked(*args):
        raise PermissionError("resolution failed")

    monkeypatch.setattr(sec_downloader, "resolves_within", locked)
    with pytest.raises(sec_downloader.SecDownloadIoError) as exc_info:
        sec_downloader.quarantine_failed_download(pending, cache_root=tmp_path)
    assert isinstance(exc_info.value.__cause__, PermissionError)


def test_download_batch_continues_after_locked_cached_filing(tmp_path, monkeypatch):
    first_row = parse_master_index(INDEX_TWO_ROWS)[0]
    client = make_client(make_transport())
    pending = sec_downloader.fetch_filing(first_row, client=client, cache_root=tmp_path)
    sec_downloader.promote_validated_filing(pending, cache_root=tmp_path)
    original = Path.read_bytes

    def locked(self):
        if self == pending.content_path:
            raise PermissionError("first filing locked")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    seen = []
    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=client,
        cache_root=tmp_path,
        on_filing_parsed=lambda row, filing: seen.append(row.cik),
    )
    assert (report.discovered, report.failed, report.parsed) == (2, 1, 1)
    assert report.dates_completed == 0
    assert report.outcomes[0].stage == "fetch"
    assert report.outcomes[0].status.value == "retryable_failure"
    assert seen == ["0000789019"]

    # Unlocking the cache must let the same date retry the missed filing.
    monkeypatch.setattr(Path, "read_bytes", original)
    seen.clear()
    retried = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=client,
        cache_root=tmp_path,
        on_filing_parsed=lambda row, filing: seen.append(row.cik),
    )
    assert retried.dates_completed == 1
    assert retried.failed == 0
    assert seen == ["0000320193", "0000789019"]
