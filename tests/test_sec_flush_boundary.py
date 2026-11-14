"""Buffered filing sinks commit before SEC date completion becomes visible."""

from __future__ import annotations

import pytest

from insider_scanner.core.sec_client import SecBlockedError
from insider_scanner.persistence.json_state import read_json_dict
from insider_scanner.services.sec_downloads import (
    SecFilingOutcome,
    SecFilingStatus,
    download_filings_in_range,
)
from tests.sec_stubs import DAY, StubResponse, make_client, make_transport


def test_flush_precedes_checkpoint_and_progress(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    pending = []
    committed = []
    snapshots = []

    def flush():
        assert not checkpoint.exists()
        committed.extend(pending)
        pending.clear()
        return ()

    def progress(snapshot):
        assert len(committed) == 2
        assert not pending
        assert read_json_dict(checkpoint)["completed_dates"] == [DAY.isoformat()]
        snapshots.append(snapshot)

    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path / "cache",
        checkpoint_path=checkpoint,
        on_filing_parsed=lambda row, filing: pending.append(row),
        on_date_flush=flush,
        on_progress=progress,
    )

    assert report.dates_completed == 1
    assert len(snapshots) == 1


def test_flush_failures_keep_date_retryable_and_count_each_filing(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    failures = tuple(
        SecFilingOutcome(
            accession,
            SecFilingStatus.RETRYABLE_FAILURE,
            "persist",
            "database_locked",
        )
        for accession in ("0000320193-26-000061", "0000789019-26-000062")
    )
    snapshots = []

    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path / "cache",
        checkpoint_path=checkpoint,
        on_date_flush=lambda: failures,
        on_progress=snapshots.append,
        continue_on_error=False,
    )

    assert report.failed == 2
    assert report.outcomes == failures
    assert report.dates_completed == 0
    assert report.stopped_on_error
    assert not checkpoint.exists()
    assert snapshots == []


def test_cancelled_date_flushes_valid_pending_filings_without_completion(tmp_path):
    pending = []
    committed = []

    def flush():
        committed.extend(pending)
        pending.clear()
        return ()

    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport()),
        cache_root=tmp_path,
        on_filing_parsed=lambda row, filing: pending.append(row),
        on_date_flush=flush,
        cancelled=lambda: bool(pending),
    )

    assert report.cancelled
    assert report.dates_completed == 0
    assert report.parsed == len(committed) == 1
    assert not pending


def test_partial_date_flushes_successes_despite_another_fetch_failure(tmp_path):
    transport = make_transport()
    original_get = transport.get.side_effect
    transport.get.side_effect = lambda url, **kwargs: (
        StubResponse(status_code=503)
        if "/789019/" in url
        else original_get(url, **kwargs)
    )
    pending = []
    committed = []

    def flush():
        committed.extend(pending)
        pending.clear()
        return ()

    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(transport),
        cache_root=tmp_path,
        on_filing_parsed=lambda row, filing: pending.append(row),
        on_date_flush=flush,
    )

    assert report.failed == 1
    assert report.dates_completed == 0
    assert len(committed) == 1
    assert not pending


@pytest.mark.parametrize("flush_raises", [False, True])
def test_blocked_date_flushes_pending_without_masking_original_exception(
    tmp_path, monkeypatch, caplog, flush_raises
):
    from insider_scanner.services import sec_downloads

    original_fetch = sec_downloads.fetch_filing
    pending = []
    committed = []

    def fetch(row, **kwargs):
        if pending:
            raise SecBlockedError(600)
        return original_fetch(row, **kwargs)

    def flush():
        committed.extend(pending)
        pending.clear()
        if flush_raises:
            raise RuntimeError("flush failed with private adapter details")
        return ()

    monkeypatch.setattr(sec_downloads, "fetch_filing", fetch)
    checkpoint = tmp_path / "checkpoint.json"
    with pytest.raises(SecBlockedError):
        download_filings_in_range(
            start_date=DAY,
            end_date=DAY,
            client=make_client(make_transport()),
            cache_root=tmp_path / "cache",
            checkpoint_path=checkpoint,
            on_filing_parsed=lambda row, filing: pending.append(row),
            on_date_flush=flush,
        )

    assert len(committed) == 1
    assert not pending
    assert not checkpoint.exists()
    if flush_raises:
        assert "RuntimeError" in caplog.text
        assert "private adapter details" not in caplog.text
