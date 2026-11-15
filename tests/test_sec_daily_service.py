"""Behavior tests for the idempotent SEC daily ingestion service.

Reuses the fake transport/client construction and the ``sec_form4_primary.xml``
fixture from :mod:`tests.sec_stubs`, paired with a temp-file SQLite
``PersistenceContext`` so every test runs without live network or a shared DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ownership_parser import OwnershipFiling
from insider_scanner.core.sec_trade_mapping import SecTradeMappingError
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.services.context import PersistenceContext
from insider_scanner.services.sec_daily import (
    SEC_DAILY_IDENTIFIER,
    SecDailyIngestionService,
    SecDailyIngestionSummary,
)
from insider_scanner.services.sec_downloads import SecDownloadProgress
from tests.sec_stubs import (
    DAY,
    EMPTY_INDEX,
    INDEX_DAY_TWO,
    INDEX_MIXED,
    INDEX_ONE_ROW,
    INDEX_TWO_ROWS,
    NEXT_DAY,
    make_client,
    make_transport,
)


def _make_service(
    persistence: PersistenceContext,
    tmp_path: Path,
    transport: object,
    **kwargs: object,
) -> SecDailyIngestionService:
    return SecDailyIngestionService(
        persistence,
        client=make_client(transport),  # type: ignore[arg-type]
        cache_root=tmp_path / "cache",
        **kwargs,  # type: ignore[arg-type]
    )


def _coverage(persistence: PersistenceContext) -> tuple[object, ...]:
    return persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar")


def _sec_rows(persistence: PersistenceContext) -> list[object]:
    return list(persistence.us_trades.query_latest(100, sources="sec_edgar"))


# ---------------------------------------------------------------------------
# 1. Empty index day
# ---------------------------------------------------------------------------


def test_empty_index_records_coverage_with_zero_rows(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    service = _make_service(
        persistence, tmp_path, make_transport(index_text=EMPTY_INDEX)
    )

    summary = service.ingest_date(DAY)

    assert isinstance(summary, SecDailyIngestionSummary)
    assert summary.dates_total == 1
    assert summary.dates_completed == 1
    assert summary.filings_discovered == 0
    assert summary.filings_parsed == 0
    assert summary.transactions_inserted == 0
    assert summary.failures == 0
    coverage = _coverage(persistence)
    assert len(coverage) == 1
    assert coverage[0].start == DAY  # type: ignore[attr-defined]
    assert coverage[0].end == DAY  # type: ignore[attr-defined]
    assert _sec_rows(persistence) == []


# ---------------------------------------------------------------------------
# 2. Mixed index → only ownership forms processed
# ---------------------------------------------------------------------------


def test_mixed_index_processes_only_ownership_forms(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    service = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_MIXED)
    )

    summary = service.ingest_date(DAY)

    # The 8-K row is filtered by the index parser → only the Form 4 is seen.
    assert summary.filings_discovered == 1
    assert summary.filings_parsed == 1
    assert summary.transactions_inserted == 1
    assert summary.failures == 0
    assert len(_sec_rows(persistence)) == 1


# ---------------------------------------------------------------------------
# 3. Failed download (transport error for a filing)
# ---------------------------------------------------------------------------


def test_failed_filing_download_is_counted_and_run_continues(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    # The filing document fetch returns a non-200 → recoverable fetch failure.
    transport = make_transport(index_text=INDEX_TWO_ROWS)

    def _get(url: str, **_kwargs: object):
        from tests.sec_stubs import StubResponse

        if url.endswith(".idx"):
            return StubResponse(chunks=(INDEX_TWO_ROWS.encode("utf-8"),))
        # Both filing documents fail to fetch (500).
        return StubResponse(status_code=500, chunks=())

    transport.get.side_effect = _get

    service = _make_service(persistence, tmp_path, transport)
    summary = service.ingest_date(DAY)

    assert summary.failures == 2
    assert summary.filings_parsed == 0
    # Temporary fetch failures leave the date available for a later retry.
    assert summary.dates_completed == 0
    assert _coverage(persistence) == ()
    assert len(summary.outcomes) == 2
    assert all(not item.complete for item in summary.outcomes)
    assert _sec_rows(persistence) == []


# ---------------------------------------------------------------------------
# 4. Parser failure → filing not ingested, date still completes
# ---------------------------------------------------------------------------


def test_parser_failure_skips_filing_but_date_completes(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    service = _make_service(
        persistence,
        tmp_path,
        make_transport(
            index_text=INDEX_TWO_ROWS, filing_bytes=b"not an ownership document"
        ),
    )

    summary = service.ingest_date(DAY)

    # Parse failures are handled in the downloader before the sink runs.
    assert summary.filings_parsed == 0
    assert summary.failures == 2
    assert summary.dates_completed == 1
    assert len(_coverage(persistence)) == 1
    assert _sec_rows(persistence) == []


# ---------------------------------------------------------------------------
# 5. Re-run same date is idempotent (skips, no re-download)
# ---------------------------------------------------------------------------


def test_rerun_same_date_is_idempotent_without_redownload(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    first = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_TWO_ROWS)
    )
    first_summary = first.ingest_date(DAY)
    assert first_summary.transactions_inserted == 2
    assert len(_sec_rows(persistence)) == 2

    # Second run: the date is already covered → gaps empty → no download at all.
    second_fetched: list[str] = []
    second = _make_service(
        persistence,
        tmp_path,
        make_transport(index_text=INDEX_TWO_ROWS, fetched=second_fetched),
    )
    second_summary = second.ingest_date(DAY)

    assert second_fetched == []  # nothing fetched: download skipped entirely
    assert second_summary.filings_discovered == 0
    assert second_summary.transactions_inserted == 0
    assert second_summary.transactions_skipped == 0  # sink never ran
    assert second_summary.dates_completed == 1  # already-covered day counts
    assert len(_sec_rows(persistence)) == 2  # no new rows


def test_ledger_skips_committed_filings_when_date_coverage_is_missing(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    """A lost coverage update re-reads the index without re-fetching committed filings."""
    service = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_TWO_ROWS)
    )
    service.ingest_date(DAY)
    assert len(_sec_rows(persistence)) == 2

    # Simulate interruption between the filing commit and coverage bookkeeping.
    from sqlalchemy import text

    with persistence.engine.begin() as connection:
        connection.execute(text("DELETE FROM scan_coverage"))

    fetched = []
    second = _make_service(
        persistence,
        tmp_path,
        make_transport(index_text=INDEX_TWO_ROWS, fetched=fetched),
    )
    summary = second.ingest_date(DAY)

    assert len(fetched) == 1
    assert fetched[0].endswith("master.20260615.idx")
    assert summary.filings_parsed == 0
    assert summary.dates_completed == 1
    assert summary.transactions_inserted == 0
    assert summary.transactions_skipped == 0
    assert len(_sec_rows(persistence)) == 2  # still idempotent


# ---------------------------------------------------------------------------
# 6. Range catch-up processes gap dates in calendar order
# ---------------------------------------------------------------------------


def test_range_catchup_records_coverage_for_each_day(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    # Day one yields the Form 4 fixture; day two yields its own ownership row.
    def transport_for_range():
        from tests.sec_stubs import VALID_FILING, StubResponse

        def _get(url: str, **_kwargs: object) -> StubResponse:
            if url.endswith(".idx"):
                if "20260615" in url:
                    return StubResponse(chunks=(INDEX_ONE_ROW.encode("utf-8"),))
                if "20260616" in url:
                    return StubResponse(chunks=(INDEX_DAY_TWO.encode("utf-8"),))
                return StubResponse(chunks=(EMPTY_INDEX.encode("utf-8"),))
            return StubResponse(
                chunks=(VALID_FILING,), headers={"Content-Type": "application/xml"}
            )

        from unittest.mock import Mock

        from insider_scanner.core.sec_client import SecTransport

        transport = Mock(spec=SecTransport)
        transport.get.side_effect = _get
        return transport

    service = _make_service(persistence, tmp_path, transport_for_range())
    summary = service.ingest_range(DAY, NEXT_DAY)

    assert summary.dates_total == 2
    assert summary.dates_completed == 2
    assert summary.filings_parsed == 2
    assert summary.transactions_inserted == 2
    # Two consecutive covered days compact into a single interval.
    coverage = persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar")
    assert len(coverage) == 1
    assert coverage[0].start == DAY
    assert coverage[0].end == NEXT_DAY


# ---------------------------------------------------------------------------
# 7. Mid-date cancel leaves that date uncovered; follow-up completes it
# ---------------------------------------------------------------------------


def test_mid_date_cancel_leaves_date_uncovered_then_resumes(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    def transport_for_range():
        from unittest.mock import Mock

        from insider_scanner.core.sec_client import SecTransport
        from tests.sec_stubs import VALID_FILING, StubResponse

        def _get(url: str, **_kwargs: object) -> StubResponse:
            if url.endswith(".idx"):
                if "20260615" in url:
                    return StubResponse(chunks=(INDEX_ONE_ROW.encode("utf-8"),))
                if "20260616" in url:
                    return StubResponse(chunks=(INDEX_DAY_TWO.encode("utf-8"),))
                return StubResponse(chunks=(EMPTY_INDEX.encode("utf-8"),))
            return StubResponse(
                chunks=(VALID_FILING,), headers={"Content-Type": "application/xml"}
            )

        transport = Mock(spec=SecTransport)
        transport.get.side_effect = _get
        return transport

    service = _make_service(persistence, tmp_path, transport_for_range())

    # A cancel callable that flips true once the first date is covered. The
    # downloader records coverage via on_progress only after a date fully
    # completes, so this fires between dates: day one completes, day two is
    # cancelled before it runs and stays uncovered for the follow-up run.
    def cancelled() -> bool:
        covered = persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar")
        return any(interval.start <= DAY <= interval.end for interval in covered)

    summary = service.ingest_range(DAY, NEXT_DAY, cancelled=cancelled)

    assert summary.dates_total == 2
    assert summary.dates_completed == 1  # only the first date completed
    covered = persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar")
    assert any(i.start <= DAY <= i.end for i in covered)
    assert not any(i.start <= NEXT_DAY <= i.end for i in covered)

    # Follow-up run completes the remaining (uncovered) date.
    resume = _make_service(persistence, tmp_path, transport_for_range())
    resume_summary = resume.ingest_range(DAY, NEXT_DAY)

    assert resume_summary.dates_completed == 2
    final_cov = persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar")
    assert len(final_cov) == 1
    assert final_cov[0].start == DAY
    assert final_cov[0].end == NEXT_DAY


# ---------------------------------------------------------------------------
# 8. Cache cleanup runs on clean completion, can be disabled
# ---------------------------------------------------------------------------


def test_cleanup_runs_on_clean_completion(
    persistence: PersistenceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    def _fake_purge(*a: object, **k: object) -> int:
        calls.append((a, k))
        return 0

    monkeypatch.setattr(
        "insider_scanner.services.sec_downloads.purge_stale_cache",
        _fake_purge,
    )

    service = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_ONE_ROW), cleanup=True
    )
    service.ingest_date(DAY)

    assert len(calls) == 1


def test_cleanup_can_be_disabled(
    persistence: PersistenceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    def _fake_purge(*a: object, **k: object) -> int:
        calls.append((a, k))
        return 0

    monkeypatch.setattr(
        "insider_scanner.services.sec_downloads.purge_stale_cache",
        _fake_purge,
    )

    service = _make_service(
        persistence,
        tmp_path,
        make_transport(index_text=INDEX_ONE_ROW),
        cleanup=False,
    )
    service.ingest_date(DAY)

    assert calls == []


# ---------------------------------------------------------------------------
# 9. Persist-sink failure paths
# ---------------------------------------------------------------------------


def test_persistence_error_in_sink_leaves_date_uncovered_then_recovers(
    persistence: PersistenceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PersistenceError from upsert propagates → date-level retry.

    The date is left uncovered, ``failures`` reflects it, and a subsequent run
    with a working upsert completes and covers the date.
    """
    original_upsert = persistence.sec_ingestion.commit

    def failing_upsert(trades: object) -> object:
        raise PersistenceError("simulated transient DB failure")

    monkeypatch.setattr(persistence.sec_ingestion, "commit", failing_upsert)

    service = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_TWO_ROWS)
    )
    summary = service.ingest_date(DAY)

    # Each filing's sink raised PersistenceError → counted as a download failure.
    assert summary.failures == 2
    # The date is left uncovered (coverage NOT recorded).
    assert _coverage(persistence) == ()
    assert summary.dates_completed == 0

    # Restore the working upsert and re-run: the date now completes and is covered.
    monkeypatch.setattr(persistence.sec_ingestion, "commit", original_upsert)
    recovery = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_TWO_ROWS)
    )
    recovery_summary = recovery.ingest_date(DAY)

    assert recovery_summary.dates_completed == 1
    assert recovery_summary.failures == 0
    assert len(_coverage(persistence)) == 1
    assert len(_sec_rows(persistence)) == 2


def test_mapping_error_leaves_date_uncovered_for_repaired_mapper(
    persistence: PersistenceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mapping errors remain retryable after a mapper fix."""
    calls = {"n": 0}

    def mapping_raises(
        filing: OwnershipFiling, index_row: SecMasterIndexRow
    ) -> tuple[object, ...]:
        calls["n"] += 1
        raise SecTradeMappingError("malformed filing")

    monkeypatch.setattr(
        "insider_scanner.services.sec_persistence.ownership_filing_to_trades",
        mapping_raises,
    )

    service = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_TWO_ROWS)
    )
    summary = service.ingest_date(DAY)

    # Both filings parsed, with accession-specific retry outcomes.
    assert calls["n"] == 2
    assert summary.filings_parsed == 2
    assert summary.failures == 2
    assert summary.dates_completed == 0
    assert _coverage(persistence) == ()
    assert all(item.stage == "mapping" for item in summary.outcomes)
    # No rows persisted (mapping never produced trades).
    assert _sec_rows(persistence) == []
    monkeypatch.undo()
    recovered = service.ingest_date(DAY)
    assert recovered.transactions_inserted == 2
    assert recovered.dates_completed == 1


def test_stop_on_error_does_not_start_later_coverage_gap(persistence, tmp_path):
    from datetime import timedelta

    from insider_scanner.persistence.coverage import DateInterval

    middle = DAY + timedelta(days=1)
    end = DAY + timedelta(days=2)
    persistence.coverage.add(
        "us", SEC_DAILY_IDENTIFIER, "sec_edgar", DateInterval(middle, middle)
    )
    fetched = []
    service = _make_service(
        persistence,
        tmp_path,
        make_transport(index_status=500, fetched=fetched),
        continue_on_error=False,
    )
    summary = service.ingest_range(DAY, end)
    assert summary.stopped_on_error
    assert not summary.cancelled
    assert summary.dates_completed == 1  # Only the pre-existing covered day.
    assert len(set(fetched)) == 1
    assert "20260615" in fetched[0]


def test_partial_date_persists_good_filing_and_retries_failed_one(
    persistence, tmp_path
):
    from tests.sec_stubs import StubResponse

    transport = make_transport()
    original_get = transport.get.side_effect

    def get(url, **kwargs):
        if "/789019/" in url:
            return StubResponse(status_code=503)
        return original_get(url, **kwargs)

    transport.get.side_effect = get
    service = _make_service(persistence, tmp_path, transport)
    first = service.ingest_date(DAY)
    assert first.transactions_inserted == 1
    assert first.failures == 1
    assert first.dates_completed == 0
    assert _coverage(persistence) == ()
    assert len(_sec_rows(persistence)) == 1

    second = _make_service(persistence, tmp_path, make_transport()).ingest_date(DAY)
    assert second.dates_completed == 1
    assert second.transactions_inserted == 1
    assert len(_sec_rows(persistence)) == 2


# ---------------------------------------------------------------------------
# 10. on_progress passthrough (WS-A): callers observe per-date progress
# ---------------------------------------------------------------------------


def _two_day_transport() -> object:
    """Mock transport serving a single Form 4 on each of the two range days."""
    from unittest.mock import Mock

    from insider_scanner.core.sec_client import SecTransport
    from tests.sec_stubs import VALID_FILING, StubResponse

    def _get(url: str, **_kwargs: object) -> StubResponse:
        if url.endswith(".idx"):
            if "20260615" in url:
                return StubResponse(chunks=(INDEX_ONE_ROW.encode("utf-8"),))
            if "20260616" in url:
                return StubResponse(chunks=(INDEX_DAY_TWO.encode("utf-8"),))
            return StubResponse(chunks=(EMPTY_INDEX.encode("utf-8"),))
        return StubResponse(
            chunks=(VALID_FILING,), headers={"Content-Type": "application/xml"}
        )

    transport = Mock(spec=SecTransport)
    transport.get.side_effect = _get
    return transport


def test_ingest_range_emits_one_progress_per_completed_date(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    service = _make_service(persistence, tmp_path, _two_day_transport())
    snapshots: list[SecDownloadProgress] = []

    summary = service.ingest_range(DAY, NEXT_DAY, on_progress=snapshots.append)

    assert all(isinstance(s, SecDownloadProgress) for s in snapshots)
    assert [s.current_date for s in snapshots] == [DAY, NEXT_DAY]
    # Coverage is still recorded even though a user callback was injected.
    assert summary.dates_completed == 2
    coverage = _coverage(persistence)
    assert len(coverage) == 1
    assert coverage[0].start == DAY  # type: ignore[attr-defined]
    assert coverage[0].end == NEXT_DAY  # type: ignore[attr-defined]


def test_ingest_date_forwards_progress_for_the_single_day(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    service = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_ONE_ROW)
    )
    snapshots: list[SecDownloadProgress] = []

    service.ingest_date(DAY, on_progress=snapshots.append)

    assert [s.current_date for s in snapshots] == [DAY]


def test_coverage_is_recorded_before_the_user_callback_fires(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    service = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_ONE_ROW)
    )
    covered_at_callback: list[bool] = []

    def on_progress(snapshot: SecDownloadProgress) -> None:
        covered = _coverage(persistence)
        covered_at_callback.append(
            any(i.start <= snapshot.current_date <= i.end for i in covered)  # type: ignore[attr-defined]
        )

    service.ingest_date(DAY, on_progress=on_progress)

    # The recorder ran before the user callback, so coverage was already present.
    assert covered_at_callback == [True]


def test_failing_user_callback_does_not_lose_already_recorded_coverage(
    persistence: PersistenceContext, tmp_path: Path
) -> None:
    service = _make_service(
        persistence, tmp_path, make_transport(index_text=INDEX_ONE_ROW)
    )

    def boom(_snapshot: SecDownloadProgress) -> None:
        raise RuntimeError("user progress callback failed")

    with pytest.raises(RuntimeError):
        service.ingest_date(DAY, on_progress=boom)

    # Coverage was recorded before the failing callback ran, so it survives.
    covered = _coverage(persistence)
    assert any(i.start <= DAY <= i.end for i in covered)  # type: ignore[attr-defined]


def test_partial_date_retry_fetches_only_failed_accession(persistence, tmp_path):
    from insider_scanner.core.sec_ingestion import SecFilingStatus
    from tests.sec_stubs import StubResponse

    first_accession = "0000320193-26-000061"
    failed_accession = "0000789019-26-000010"
    transport = make_transport(index_text=INDEX_TWO_ROWS)
    original_get = transport.get.side_effect
    transport.get.side_effect = lambda url, **kwargs: (
        StubResponse(status_code=503)
        if "/789019/" in url
        else original_get(url, **kwargs)
    )
    first = _make_service(persistence, tmp_path, transport).ingest_date(DAY)
    assert first.failures == first.transactions_inserted == 1
    assert first.dates_completed == 0
    assert (
        persistence.sec_ingestion.get(first_accession).status is SecFilingStatus.SUCCESS
    )
    assert (
        persistence.sec_ingestion.get(failed_accession).status
        is SecFilingStatus.RETRYABLE_FAILURE
    )
    assert persistence.sec_ingestion.get(failed_accession).attempts == 1

    fetched = []
    second = _make_service(
        persistence,
        tmp_path,
        make_transport(index_text=INDEX_TWO_ROWS, fetched=fetched),
    ).ingest_date(DAY)
    assert second.filings_discovered == 2
    assert (
        second.filings_parsed
        == second.transactions_inserted
        == second.dates_completed
        == 1
    )
    assert second.failures == 0
    assert len(fetched) == 2  # index and the previously failed filing
    assert all(first_accession not in url for url in fetched)
    assert persistence.sec_ingestion.get(first_accession).attempts == 1
    assert persistence.sec_ingestion.get(failed_accession).attempts == 2
    assert persistence.sec_ingestion.is_terminal(failed_accession)


def test_blocked_filing_flushes_success_and_records_retry(persistence, tmp_path):
    from insider_scanner.core.sec_client import SecBlockedError
    from insider_scanner.core.sec_ingestion import SecFilingStatus
    from tests.sec_stubs import StubResponse

    transport = make_transport(index_text=INDEX_TWO_ROWS)
    original_get = transport.get.side_effect
    transport.get.side_effect = lambda url, **kwargs: (
        StubResponse(status_code=403)
        if "/789019/" in url
        else original_get(url, **kwargs)
    )
    with pytest.raises(SecBlockedError):
        _make_service(persistence, tmp_path, transport).ingest_date(DAY)

    assert len(_sec_rows(persistence)) == 1
    assert _coverage(persistence) == ()
    assert persistence.sec_ingestion.is_terminal("0000320193-26-000061")
    failure = persistence.sec_ingestion.get("0000789019-26-000010")
    assert failure.status is SecFilingStatus.RETRYABLE_FAILURE
    assert failure.attempts == 1
    assert failure.reason == "SecBlockedError"


def test_failed_terminal_outcome_commit_keeps_date_uncovered(
    persistence, tmp_path, monkeypatch
):
    legacy = (
        b"<SEC-DOCUMENT>\n<DOCUMENT>\n<TYPE>4\n<TEXT>Paper filing</TEXT>\n</DOCUMENT>"
    )
    original_commit = persistence.sec_ingestion.commit

    def fail(entries):
        raise PersistenceError("database is locked")

    monkeypatch.setattr(persistence.sec_ingestion, "commit", fail)
    first = _make_service(
        persistence,
        tmp_path,
        make_transport(index_text=INDEX_ONE_ROW, filing_bytes=legacy),
    ).ingest_date(DAY)
    assert first.unsupported == first.failures == 1
    assert first.dates_completed == 0
    assert _coverage(persistence) == ()
    assert persistence.sec_ingestion.get("0000320193-26-000061") is None

    monkeypatch.setattr(persistence.sec_ingestion, "commit", original_commit)
    second = _make_service(
        persistence,
        tmp_path,
        make_transport(index_text=INDEX_ONE_ROW, filing_bytes=legacy),
    ).ingest_date(DAY)
    assert second.failures == 0
    assert second.unsupported == second.dates_completed == 1
    assert persistence.sec_ingestion.is_terminal("0000320193-26-000061")
