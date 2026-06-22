"""Behavior tests for the resumable SEC download/parse orchestrator."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest

from insider_scanner.core.sec_client import SecClient, SecTransport
from insider_scanner.core.sec_fair_access import RateLimiter
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ownership_parser import OwnershipFiling
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.json_state import atomic_write_json
from insider_scanner.services.sec_downloads import (
    SecDownloadProgress,
    SecDownloadReport,
    download_filings_in_range,
)

VALID_USER_AGENT = "Insider Scanner ops@insider-scanner.example"
FIXTURES = Path(__file__).parent / "fixtures"
VALID_FILING = (FIXTURES / "sec_form4_primary.xml").read_bytes()

INDEX_TWO_ROWS = (
    "Description: daily index\n"
    "CIK|Company Name|Form Type|Date Filed|Filename\n"
    "-" * 60 + "\n"
    "320193|APPLE INC|4|2026-06-15|edgar/data/320193/0000320193-26-000061.txt\n"
    "789019|MICROSOFT CORP|4|2026-06-15|edgar/data/789019/0000789019-26-000010.txt\n"
)


@dataclass(slots=True)
class StubResponse:
    status_code: int = 200
    chunks: tuple[bytes, ...] = (b"",)
    headers: Mapping[str, str] = field(
        default_factory=lambda: {"Content-Type": "text/plain"}
    )
    close_calls: int = 0

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        yield from self.chunks

    def close(self) -> None:
        self.close_calls += 1


def make_transport(
    *,
    index_text: str = INDEX_TWO_ROWS,
    filing_bytes: bytes = VALID_FILING,
    index_status: int = 200,
    fetched: list[str] | None = None,
) -> Mock:
    def _get(url: str, **_kwargs: object) -> StubResponse:
        if fetched is not None:
            fetched.append(url)
        if url.endswith(".idx"):
            if index_status != 200:
                return StubResponse(status_code=index_status, chunks=())
            return StubResponse(
                chunks=(index_text.encode("utf-8"),),
                headers={"Content-Type": "text/plain"},
            )
        return StubResponse(
            chunks=(filing_bytes,), headers={"Content-Type": "application/xml"}
        )

    transport = Mock(spec=SecTransport)
    transport.get.side_effect = _get
    return transport


def make_client(transport: Mock) -> SecClient:
    return SecClient(
        user_agent=VALID_USER_AGENT,
        transport=cast(SecTransport, transport),
        rate_limiter=RateLimiter(min_interval_seconds=0.0),
    )


DAY = date(2026, 6, 15)


def test_single_day_reports_discovered_downloaded_parsed(tmp_path: Path) -> None:
    client = make_client(make_transport())

    report = download_filings_in_range(
        start_date=DAY, end_date=DAY, client=client, cache_root=tmp_path
    )

    assert isinstance(report, SecDownloadReport)
    assert (report.discovered, report.downloaded, report.parsed) == (2, 2, 2)
    assert (report.skipped, report.failed) == (0, 0)
    assert report.dates_total == 1
    assert report.dates_completed == 1


def test_fresh_cache_hits_are_counted_as_skipped(tmp_path: Path) -> None:
    download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
    )

    # Second run over the same range: filings are served from the validated cache.
    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
    )

    assert report.skipped == 2
    assert report.downloaded == 0
    assert report.parsed == 2


def test_absent_daily_index_is_an_empty_day_not_a_failure(tmp_path: Path) -> None:
    client = make_client(make_transport(index_status=404))

    report = download_filings_in_range(
        start_date=DAY, end_date=DAY, client=client, cache_root=tmp_path
    )

    assert report.discovered == 0
    assert report.failed == 0
    assert report.dates_completed == 1


def test_unparseable_filings_are_counted_failed_and_run_continues(
    tmp_path: Path,
) -> None:
    client = make_client(make_transport(filing_bytes=b"not an ownership document"))

    report = download_filings_in_range(
        start_date=DAY, end_date=DAY, client=client, cache_root=tmp_path
    )

    assert report.discovered == 2
    assert report.downloaded == 2
    assert report.failed == 2
    assert report.parsed == 0
    assert report.dates_completed == 1


def test_progress_callback_receives_cumulative_snapshot(tmp_path: Path) -> None:
    snapshots: list[SecDownloadProgress] = []

    download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
        on_progress=snapshots.append,
    )

    assert len(snapshots) == 1
    assert snapshots[0].current_date == DAY
    assert snapshots[0].parsed == 2
    assert snapshots[0].dates_completed == 1


class _CancelAfterDates:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.completed = 0

    def __call__(self) -> bool:
        return self.completed >= self.limit


def test_cancellation_leaves_remaining_dates_for_resume(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    cancel = _CancelAfterDates(1)

    def progress(snapshot: SecDownloadProgress) -> None:
        cancel.completed = snapshot.dates_completed

    first = download_filings_in_range(
        start_date=date(2026, 6, 15),
        end_date=date(2026, 6, 16),
        client=make_client(make_transport()),
        cache_root=tmp_path,
        checkpoint_path=checkpoint,
        cancelled=cancel,
        on_progress=progress,
    )

    assert first.dates_total == 2
    assert first.dates_completed == 1
    assert checkpoint.exists()  # retained for resume


def test_resume_skips_completed_dates_and_restores_counters(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    cancel = _CancelAfterDates(1)

    def progress(snapshot: SecDownloadProgress) -> None:
        cancel.completed = snapshot.dates_completed

    download_filings_in_range(
        start_date=date(2026, 6, 15),
        end_date=date(2026, 6, 16),
        client=make_client(make_transport()),
        cache_root=tmp_path,
        checkpoint_path=checkpoint,
        cancelled=cancel,
        on_progress=progress,
    )

    resume_urls: list[str] = []
    report = download_filings_in_range(
        start_date=date(2026, 6, 15),
        end_date=date(2026, 6, 16),
        client=make_client(make_transport(fetched=resume_urls)),
        cache_root=tmp_path,
        checkpoint_path=checkpoint,
    )

    assert report.dates_completed == 2
    assert report.discovered == 4  # 2 from run one (restored) + 2 from the resume
    # The already-completed first day is never re-fetched on resume.
    assert not any("20260615" in url for url in resume_urls)
    assert any("20260616" in url for url in resume_urls)
    assert not checkpoint.exists()  # deleted on clean completion


def test_mismatched_checkpoint_range_starts_fresh(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    # A stale checkpoint for a *different* range that marks its day complete.
    atomic_write_json(
        checkpoint,
        {
            "version": 1,
            "range": {"start": "2026-06-15", "end": "2026-06-15"},
            "completed_dates": ["2026-06-15"],
            "counters": {key: 99 for key in (
                "discovered", "downloaded", "parsed", "skipped", "failed"
            )},
        },
    )

    report = download_filings_in_range(
        start_date=date(2026, 6, 20),
        end_date=date(2026, 6, 20),
        client=make_client(make_transport()),
        cache_root=tmp_path,
        checkpoint_path=checkpoint,
    )

    # Range differs → the stale state (including its inflated counters) is ignored.
    assert report.discovered == 2
    assert report.dates_completed == 1


def test_cleanup_runs_purge_on_clean_completion(tmp_path: Path) -> None:
    with patch(
        "insider_scanner.services.sec_downloads.purge_stale_cache", return_value=0
    ) as purge:
        download_filings_in_range(
            start_date=DAY,
            end_date=DAY,
            client=make_client(make_transport()),
            cache_root=tmp_path,
            cleanup=True,
        )

    purge.assert_called_once()


def test_cleanup_can_be_disabled(tmp_path: Path) -> None:
    with patch(
        "insider_scanner.services.sec_downloads.purge_stale_cache"
    ) as purge:
        download_filings_in_range(
            start_date=DAY,
            end_date=DAY,
            client=make_client(make_transport()),
            cache_root=tmp_path,
            cleanup=False,
        )

    purge.assert_not_called()


def test_failed_filing_quarantined_only_in_debug_mode(tmp_path: Path) -> None:
    download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport(filing_bytes=b"bad")),
        cache_root=tmp_path,
        retain_failed_downloads=True,
    )
    diagnostics = tmp_path / "diagnostics"
    assert diagnostics.is_dir()
    assert len(list(diagnostics.glob("*.raw"))) == 2


def test_failed_filing_not_retained_by_default(tmp_path: Path) -> None:
    download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport(filing_bytes=b"bad")),
        cache_root=tmp_path,
    )

    assert not (tmp_path / "diagnostics").exists()


def test_invalid_client_type_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="client must be exactly SecClient"):
        download_filings_in_range(
            start_date=DAY,
            end_date=DAY,
            client=cast(SecClient, object()),
            cache_root=tmp_path,
        )


def test_invalid_cache_root_type_is_rejected() -> None:
    client = make_client(make_transport())
    with pytest.raises(TypeError, match="cache_root must be a pathlib.Path"):
        download_filings_in_range(
            start_date=DAY,
            end_date=DAY,
            client=client,
            cache_root=cast(Path, "not-a-path"),
        )


def test_date_level_failure_continues_but_leaves_date_incomplete(
    tmp_path: Path,
) -> None:
    # A non-recoverable index status (500) fails the date without aborting.
    client = make_client(make_transport(index_status=500))

    report = download_filings_in_range(
        start_date=DAY, end_date=DAY, client=client, cache_root=tmp_path
    )

    assert report.discovered == 0
    assert report.dates_completed == 0  # not marked complete → a resume retries it


def test_continue_on_error_false_halts_on_first_failed_date(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    client = make_client(make_transport(index_status=500))

    report = download_filings_in_range(
        start_date=date(2026, 6, 15),
        end_date=date(2026, 6, 16),
        client=client,
        cache_root=tmp_path,
        checkpoint_path=checkpoint,
        continue_on_error=False,
    )

    assert report.dates_total == 2
    assert report.dates_completed == 0
    assert not checkpoint.exists()  # no date completed → nothing checkpointed


# ---------------------------------------------------------------------------
# on_filing_parsed hook tests
# ---------------------------------------------------------------------------


def test_on_filing_parsed_hook_is_called_for_each_filing(tmp_path: Path) -> None:
    """Hook receives (SecMasterIndexRow, OwnershipFiling) for every parsed filing."""
    calls: list[tuple[object, object]] = []

    def hook(row: SecMasterIndexRow, filing: OwnershipFiling) -> None:
        calls.append((row, filing))

    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
        on_filing_parsed=hook,
    )

    assert report.parsed == 2
    assert len(calls) == 2
    assert all(isinstance(row, SecMasterIndexRow) for row, _ in calls)
    assert all(isinstance(filing, OwnershipFiling) for _, filing in calls)


def test_on_filing_parsed_none_default_is_unchanged(tmp_path: Path) -> None:
    """Omitting on_filing_parsed (default None) leaves behavior identical."""
    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
        # on_filing_parsed not passed — defaults to None
    )

    assert (report.discovered, report.downloaded, report.parsed) == (2, 2, 2)
    assert (report.skipped, report.failed) == (0, 0)
    assert report.dates_completed == 1


def test_on_filing_parsed_persistence_error_leaves_date_incomplete(
    tmp_path: Path,
) -> None:
    """When the hook raises PersistenceError, failed is incremented and the date
    is NOT marked completed so it can be retried on the next run."""
    call_count = 0

    def failing_hook(row: SecMasterIndexRow, filing: OwnershipFiling) -> None:
        nonlocal call_count
        call_count += 1
        raise PersistenceError("simulated persist failure")

    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
        on_filing_parsed=failing_hook,
        continue_on_error=True,
    )

    # Hook was called for both filings — loop is not aborted early.
    assert call_count == 2
    # Each hook failure increments failed.
    assert report.failed == 2
    # The date is NOT marked completed (sink failure → retry next run).
    assert report.dates_completed == 0


def test_on_filing_parsed_persistence_error_progress_not_emitted(
    tmp_path: Path,
) -> None:
    """on_progress is not called for a date whose hook raised PersistenceError."""
    progress_dates: list[date] = []

    def failing_hook(row: SecMasterIndexRow, filing: OwnershipFiling) -> None:
        raise PersistenceError("simulated persist failure")

    download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
        on_filing_parsed=failing_hook,
        on_progress=lambda snap: progress_dates.append(snap.current_date),
    )

    assert DAY not in progress_dates


def test_on_filing_parsed_sink_failure_date_retried_on_next_run(
    tmp_path: Path,
) -> None:
    """After a hook failure the date stays uncovered; a second run without the
    failing hook completes it."""
    hook_raises = True

    def conditional_hook(row: SecMasterIndexRow, filing: OwnershipFiling) -> None:
        if hook_raises:
            raise PersistenceError("transient failure")

    first = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
        on_filing_parsed=conditional_hook,
    )
    assert first.dates_completed == 0

    # Second run: hook no longer raises.
    hook_raises = False
    second = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
        on_filing_parsed=conditional_hook,
    )
    assert second.dates_completed == 1
    assert second.parsed == 2
