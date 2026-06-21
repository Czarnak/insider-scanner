"""Behavior tests for staged SEC filing download and cache promotion."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError, dataclass, field, replace
from datetime import date
import hashlib
import os
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest

from insider_scanner.core.sec_client import SecClient, SecTransport
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceLimits,
    SecResourceProfile,
    SecSecurityReason,
)


VALID_USER_AGENT = "Insider Scanner ops@insider-scanner.example"


@dataclass(slots=True)
class StubResponse:
    status_code: int = 200
    chunks: tuple[bytes, ...] = (b"filing payload",)
    headers: Mapping[str, str] = field(
        default_factory=lambda: {"Content-Type": "application/octet-stream"}
    )
    close_calls: int = 0

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        yield from self.chunks

    def close(self) -> None:
        self.close_calls += 1


def make_row(
    archive_path: str = "edgar/data/320193/0000320193-26-000061.TXT",
) -> SecMasterIndexRow:
    return SecMasterIndexRow(
        cik="0000320193",
        company_name="APPLE INC",
        form_type="4",
        filing_date=date(2026, 6, 15),
        archive_path=archive_path,
    )


def make_client(
    content: bytes = b"filing payload", *, freshness: float | None = None
) -> tuple[SecClient, Mock]:
    transport = Mock(spec=SecTransport)
    transport.get.return_value = StubResponse(chunks=(content,))
    policy = DEFAULT_SEC_SECURITY_POLICY
    if freshness is not None:
        policy = replace(policy, cache_freshness_seconds=freshness)
    return (
        SecClient(
            user_agent=VALID_USER_AGENT,
            transport=cast(SecTransport, transport),
            policy=policy,
        ),
        transport,
    )


def expected_cache_path(row: SecMasterIndexRow, cache_root: Path) -> Path:
    digest = hashlib.sha256(row.archive_path.encode("utf-8")).hexdigest()
    return cache_root / "validated-v1" / f"{digest}.filing"


def test_pending_and_downloaded_filings_are_frozen_and_slotted(
    tmp_path: Path,
) -> None:
    from insider_scanner.core.sec_downloader import (
        DownloadedSecFiling,
        PendingSecFiling,
    )

    row = make_row()
    cache_path = expected_cache_path(row, tmp_path)
    pending = PendingSecFiling(
        row=row,
        archive_url="https://www.sec.gov/Archives/example.txt",
        content=b"payload",
        content_path=cache_path,
        from_cache=False,
        max_bytes=32 * 1024 * 1024,
    )
    downloaded = DownloadedSecFiling(
        row=row,
        archive_url=pending.archive_url,
        content_path=cache_path,
        from_cache=False,
    )

    assert not hasattr(pending, "__dict__")
    assert not hasattr(downloaded, "__dict__")
    with pytest.raises(FrozenInstanceError):
        pending.content = b"changed"  # type: ignore[misc]


def test_network_fetch_remains_memory_only_until_promotion(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import fetch_filing

    row = make_row()
    client, transport = make_client(b"private filing bytes")

    pending = fetch_filing(row, client=client, cache_root=tmp_path)

    assert pending.row is row
    assert pending.content == b"private filing bytes"
    assert pending.content_path == expected_cache_path(row, tmp_path)
    assert pending.from_cache is False
    assert not (tmp_path / "validated-v1").exists()
    transport.get.assert_called_once()


def test_validated_promotion_writes_versioned_cache_atomically(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        fetch_filing,
        promote_validated_filing,
    )

    row = make_row()
    client, _ = make_client(b"validated filing")
    pending = fetch_filing(row, client=client, cache_root=tmp_path)

    downloaded = promote_validated_filing(pending, cache_root=tmp_path)

    assert downloaded.content_path == expected_cache_path(row, tmp_path)
    assert downloaded.content_path.read_bytes() == b"validated filing"
    assert downloaded.from_cache is False
    assert tuple(downloaded.content_path.parent.iterdir()) == (
        downloaded.content_path,
    )


def test_promotion_preserves_originating_policy_size_limit(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        SecDownloadSecurityError,
        fetch_filing,
        promote_validated_filing,
    )

    limits = dict(DEFAULT_SEC_SECURITY_POLICY.resource_limits)
    limits[SecResourceProfile.FILING_DOCUMENT] = SecResourceLimits(
        frozenset({"application/octet-stream"}), 4
    )
    client, _ = make_client(b"1234")
    client = replace(
        client,
        policy=replace(DEFAULT_SEC_SECURITY_POLICY, resource_limits=limits),
    )
    pending = fetch_filing(make_row(), client=client, cache_root=tmp_path)
    forged = replace(pending, content=b"12345")

    with pytest.raises(SecDownloadSecurityError) as exc_info:
        promote_validated_filing(forged, cache_root=tmp_path)

    assert exc_info.value.reason is SecSecurityReason.RESPONSE_SIZE
    assert not pending.content_path.exists()


def test_fresh_validated_cache_is_reused_without_transport(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import fetch_filing

    row = make_row()
    cache_path = expected_cache_path(row, tmp_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"validated cache")
    client, transport = make_client(b"network filing")

    pending = fetch_filing(row, client=client, cache_root=tmp_path)

    assert pending.content == b"validated cache"
    assert pending.from_cache is True
    transport.get.assert_not_called()


def test_stale_entry_is_removed_and_network_bytes_remain_pending(
    tmp_path: Path,
) -> None:
    from insider_scanner.core.sec_downloader import fetch_filing

    row = make_row()
    cache_path = expected_cache_path(row, tmp_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"stale cache")
    os.utime(cache_path, (100.0, 100.0))
    client, transport = make_client(b"fresh network filing", freshness=10.0)

    with patch("insider_scanner.core.sec_downloader.time.time", return_value=1000.0):
        pending = fetch_filing(row, client=client, cache_root=tmp_path)

    assert pending.content == b"fresh network filing"
    assert pending.from_cache is False
    assert not cache_path.exists()
    transport.get.assert_called_once()


def test_promoting_cache_hit_is_noop(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        fetch_filing,
        promote_validated_filing,
    )

    row = make_row()
    cache_path = expected_cache_path(row, tmp_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"validated cache")
    client, _ = make_client()

    pending = fetch_filing(row, client=client, cache_root=tmp_path)
    downloaded = promote_validated_filing(pending, cache_root=tmp_path)

    assert downloaded.from_cache is True
    assert cache_path.read_bytes() == b"validated cache"


@pytest.mark.parametrize("invalid_row", [None, object(), "row"])
def test_invalid_row_fails_before_io(tmp_path: Path, invalid_row: object) -> None:
    from insider_scanner.core.sec_downloader import fetch_filing

    client, transport = make_client()

    with pytest.raises(TypeError, match="row must be exactly SecMasterIndexRow"):
        fetch_filing(
            cast(SecMasterIndexRow, invalid_row),
            client=client,
            cache_root=tmp_path,
        )

    transport.get.assert_not_called()
    assert not (tmp_path / "validated-v1").exists()


@pytest.mark.parametrize("invalid_root", [None, "cache", object()])
def test_invalid_cache_root_fails_before_transport(invalid_root: object) -> None:
    from insider_scanner.core.sec_downloader import fetch_filing

    client, transport = make_client()

    with pytest.raises(TypeError, match="cache_root must be a pathlib.Path"):
        fetch_filing(
            make_row(), client=client, cache_root=cast(Path, invalid_root)
        )

    transport.get.assert_not_called()


def test_cache_symlink_is_rejected(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        SecDownloadSecurityError,
        fetch_filing,
    )

    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "cache-link"
    try:
        root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    client, transport = make_client()

    with pytest.raises(SecDownloadSecurityError) as exc_info:
        fetch_filing(make_row(), client=client, cache_root=root)

    assert exc_info.value.reason is SecSecurityReason.CACHE_PATH
    transport.get.assert_not_called()


def test_forged_pending_path_cannot_escape_cache_root(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        PendingSecFiling,
        SecDownloadSecurityError,
        promote_validated_filing,
    )

    row = make_row()
    pending = PendingSecFiling(
        row=row,
        archive_url="https://www.sec.gov/Archives/example.txt",
        content=b"payload",
        content_path=tmp_path.parent / "escape.txt",
        from_cache=False,
        max_bytes=32 * 1024 * 1024,
    )

    with pytest.raises(SecDownloadSecurityError) as exc_info:
        promote_validated_filing(pending, cache_root=tmp_path)

    assert exc_info.value.reason is SecSecurityReason.CACHE_PATH
    assert not (tmp_path.parent / "escape.txt").exists()


def test_failed_atomic_promotion_is_sanitized_and_removes_temp(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        SecDownloadIoError,
        fetch_filing,
        promote_validated_filing,
    )

    client, _ = make_client(b"validated filing")
    pending = fetch_filing(make_row(), client=client, cache_root=tmp_path)

    with (
        patch.object(Path, "replace", side_effect=OSError("disk failure")),
        pytest.raises(SecDownloadIoError) as exc_info,
    ):
        promote_validated_filing(pending, cache_root=tmp_path)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert "disk failure" not in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)
    assert list((tmp_path / "validated-v1").glob(".sec-filing-*.tmp")) == []
    assert not pending.content_path.exists()


def test_forged_pending_row_subclass_is_rejected(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        PendingSecFiling,
        promote_validated_filing,
    )

    class DerivedRow(SecMasterIndexRow):
        pass

    row = make_row()
    derived = DerivedRow(
        row.cik,
        row.company_name,
        row.form_type,
        row.filing_date,
        row.archive_path,
    )
    pending = PendingSecFiling(
        row=derived,
        archive_url="https://www.sec.gov/Archives/example.txt",
        content=b"payload",
        content_path=expected_cache_path(row, tmp_path),
        from_cache=False,
        max_bytes=32 * 1024 * 1024,
    )

    with pytest.raises(TypeError, match="pending.row"):
        promote_validated_filing(pending, cache_root=tmp_path)


# ---------------------------------------------------------------------------
# Batch cache cleanup
# ---------------------------------------------------------------------------


def test_purge_stale_cache_removes_old_keeps_fresh(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import purge_stale_cache

    namespace = tmp_path / "validated-v1"
    namespace.mkdir(parents=True)
    stale = namespace / "old.filing"
    fresh = namespace / "new.filing"
    stale.write_bytes(b"old")
    fresh.write_bytes(b"new")
    os.utime(stale, (100.0, 100.0))

    with patch("insider_scanner.core.sec_downloader.time.time", return_value=1000.0):
        removed = purge_stale_cache(tmp_path, max_age_seconds=10.0)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_purge_stale_cache_returns_zero_when_namespace_absent(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import purge_stale_cache

    assert purge_stale_cache(tmp_path, max_age_seconds=0.0) == 0


def test_purge_stale_cache_rejects_symlinked_namespace(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        SecDownloadSecurityError,
        purge_stale_cache,
    )

    target = tmp_path / "elsewhere"
    target.mkdir()
    namespace = tmp_path / "validated-v1"
    try:
        namespace.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(SecDownloadSecurityError) as exc_info:
        purge_stale_cache(tmp_path, max_age_seconds=0.0)

    assert exc_info.value.reason is SecSecurityReason.CACHE_PATH


def test_purge_stale_cache_does_not_follow_or_remove_symlink_entries(
    tmp_path: Path,
) -> None:
    from insider_scanner.core.sec_downloader import purge_stale_cache

    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"keep me")
    namespace = tmp_path / "validated-v1"
    namespace.mkdir(parents=True)
    link = namespace / "link.filing"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    os.utime(outside, (100.0, 100.0))

    with patch("insider_scanner.core.sec_downloader.time.time", return_value=1000.0):
        removed = purge_stale_cache(tmp_path, max_age_seconds=10.0)

    assert removed == 0
    assert link.is_symlink()
    assert outside.exists()


# ---------------------------------------------------------------------------
# Diagnostic quarantine (opt-in failed-only retention)
# ---------------------------------------------------------------------------


def test_quarantine_failed_download_writes_under_diagnostics(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        fetch_filing,
        quarantine_failed_download,
    )

    row = make_row()
    client, _ = make_client(b"corrupt filing bytes")
    pending = fetch_filing(row, client=client, cache_root=tmp_path)

    target = quarantine_failed_download(pending, cache_root=tmp_path)

    digest = hashlib.sha256(row.archive_path.encode("utf-8")).hexdigest()
    assert target == tmp_path / "diagnostics" / f"{digest}.raw"
    assert target.read_bytes() == b"corrupt filing bytes"
    # Atomic write leaves no temp residue in the diagnostics directory.
    assert tuple(target.parent.iterdir()) == (target,)


def test_quarantine_rejects_symlinked_diagnostics_dir(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import (
        SecDownloadSecurityError,
        fetch_filing,
        quarantine_failed_download,
    )

    row = make_row()
    client, _ = make_client(b"bytes")
    pending = fetch_filing(row, client=client, cache_root=tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (tmp_path / "diagnostics").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(SecDownloadSecurityError) as exc_info:
        quarantine_failed_download(pending, cache_root=tmp_path)

    assert exc_info.value.reason is SecSecurityReason.CACHE_PATH
