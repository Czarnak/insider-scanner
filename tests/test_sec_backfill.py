from __future__ import annotations

import pytest

from insider_scanner.core.sec_bulk import iter_ownership_filings
from insider_scanner.core.sec_ingestion import SecFilingStatus
from insider_scanner.core.sec_trade_mapping import SecTradeMappingError
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.services.sec_backfill import (
    SecBackfillConfirmationError,
    SecBackfillService,
    SecBackfillSummary,
)
from tests.sec_stubs import (
    FIXTURE_ZIP,  # three transaction filings plus one excluded Form 3
    VALID_FILING,
    make_client,
    make_transport,
)

ACCESSIONS = tuple(
    meta.accession_number
    for meta in iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_ZIP.parent)
)

BACKFILL_FILING = VALID_FILING.replace(b"2026-06-12", b"2024-01-01")


def _service(persistence, tmp_path, transport=None, **kw) -> SecBackfillService:
    # The ZIP spans 2024-2026; its generic XML must predate every indexed filing.
    transport = transport or make_transport(filing_bytes=BACKFILL_FILING)
    return SecBackfillService(
        persistence,
        client=make_client(transport),
        cache_root=tmp_path / "cache",
        **kw,
    )


def test_run_without_confirm_raises_and_does_no_io(persistence, tmp_path):
    fetched: list[str] = []
    svc = _service(
        persistence,
        tmp_path,
        make_transport(filing_bytes=VALID_FILING, fetched=fetched),
    )
    with pytest.raises(SecBackfillConfirmationError):
        svc.run(FIXTURE_ZIP, confirm=False)
    assert fetched == []


def test_run_processes_all_fixture_records(persistence, tmp_path):
    svc = _service(persistence, tmp_path)
    summary = svc.run(FIXTURE_ZIP, confirm=True)
    assert isinstance(summary, SecBackfillSummary)
    assert summary.filings_discovered == 3
    assert summary.filings_parsed == 3
    assert not (tmp_path / "backfill.json").exists()


def test_rerun_skips_exactly_the_committed_accessions(persistence, tmp_path):
    fetched = []
    svc = _service(
        persistence,
        tmp_path,
        make_transport(filing_bytes=BACKFILL_FILING, fetched=fetched),
    )
    svc.run(FIXTURE_ZIP, confirm=True, cancelled=lambda: len(fetched) >= 1)
    assert persistence.sec_ingestion.is_terminal(ACCESSIONS[0])
    assert persistence.sec_ingestion.get(ACCESSIONS[1]) is None
    assert persistence.sec_ingestion.get(ACCESSIONS[2]) is None
    resume = _service(persistence, tmp_path)
    second = resume.run(FIXTURE_ZIP, confirm=True)
    assert second.skipped_resume == 1
    assert second.filings_parsed == 2
    assert second.transactions_inserted == 2
    assert second.failures == 0
    assert len(persistence.us_trades.query_latest(10)) == 3


def test_cik_filter_limits_scope(persistence, tmp_path):
    svc = _service(persistence, tmp_path)
    summary = svc.run(FIXTURE_ZIP, confirm=True, ciks=frozenset({"0001318605"}))
    assert summary.filings_discovered == 1


def test_cleanup_can_be_disabled(persistence, tmp_path, monkeypatch):
    svc = _service(persistence, tmp_path, cleanup=False)
    svc.run(FIXTURE_ZIP, confirm=True)
    assert not (tmp_path / "cache").exists()


def test_persistence_error_is_not_marked_complete_and_is_retried(
    persistence, tmp_path, monkeypatch
):
    def _boom(trades):
        raise PersistenceError("transient db failure")

    # Run 1: every upsert fails -> no accession is retired.
    monkeypatch.setattr(persistence.sec_ingestion, "commit", _boom)
    svc = _service(persistence, tmp_path)
    summary1 = svc.run(FIXTURE_ZIP, confirm=True)
    assert summary1.failures == 3
    assert summary1.transactions_inserted == 0
    assert all(
        persistence.sec_ingestion.get(accession) is None for accession in ACCESSIONS
    )

    # Run 2: upsert restored -> nothing skipped via resume; trades now persist.
    monkeypatch.undo()
    resume = _service(persistence, tmp_path)
    summary2 = resume.run(FIXTURE_ZIP, confirm=True)
    assert summary2.skipped_resume == 0
    assert summary2.transactions_inserted > 0


def test_mapping_failure_is_durable_and_recovers_after_mapper_fix(
    persistence, tmp_path, monkeypatch
):
    def _raise(filing, row):
        raise SecTradeMappingError("unmappable")

    monkeypatch.setattr(
        "insider_scanner.services.sec_persistence.ownership_filing_to_trades", _raise
    )
    # Run 1: process one mapping failure, then interrupt without retiring it.
    fetched = []
    svc = _service(
        persistence,
        tmp_path,
        make_transport(filing_bytes=BACKFILL_FILING, fetched=fetched),
    )
    first = svc.run(FIXTURE_ZIP, confirm=True, cancelled=lambda: len(fetched) >= 1)
    assert first.failures == 1
    record = persistence.sec_ingestion.get(ACCESSIONS[0])
    assert record.status is SecFilingStatus.RETRYABLE_FAILURE
    assert record.attempts == 1
    assert record.stage == "mapping"
    assert first.outcomes[0].stage == "mapping"
    assert first.outcomes[0].accession

    # Run 2: repaired mapper sees the failed accession again and persists it.
    monkeypatch.undo()
    resume = _service(persistence, tmp_path)
    second = resume.run(FIXTURE_ZIP, confirm=True)
    assert second.skipped_resume == 0
    assert second.transactions_inserted > 0


def test_blocked_backfill_stops_and_preserves_committed_ledger(persistence, tmp_path):
    import shutil

    from insider_scanner.core.sec_client import SecBlockedError
    from tests.sec_stubs import StubResponse

    transport = make_transport()
    original_get = transport.get.side_effect
    fetched = []

    def get(url, **kwargs):
        fetched.append(url)
        if len(fetched) > 1:
            return StubResponse(status_code=403)
        return original_get(url, **kwargs)

    transport.get.side_effect = get
    archive = tmp_path / "submissions.zip"
    shutil.copyfile(FIXTURE_ZIP, archive)
    service = _service(persistence, tmp_path, transport)
    with pytest.raises(SecBlockedError):
        service.run(archive, confirm=True)

    assert persistence.sec_ingestion.is_terminal(ACCESSIONS[0])
    assert (
        persistence.sec_ingestion.get(ACCESSIONS[1]).status
        is SecFilingStatus.RETRYABLE_FAILURE
    )
    assert persistence.sec_ingestion.get(ACCESSIONS[2]) is None
    assert len(fetched) == 3
    archive.rename(tmp_path / "closed-archive.zip")  # ZIP handle closed on abort


def test_partial_backfill_keeps_successful_ledger_entries_for_retry(
    persistence, tmp_path
):
    from tests.sec_stubs import StubResponse

    transport = make_transport(filing_bytes=BACKFILL_FILING)
    original_get = transport.get.side_effect

    def get(url, **kwargs):
        if "/1318605/" in url:
            return StubResponse(status_code=503)
        return original_get(url, **kwargs)

    transport.get.side_effect = get
    first = _service(persistence, tmp_path, transport).run(FIXTURE_ZIP, confirm=True)
    assert first.failures == 1
    assert [
        persistence.sec_ingestion.is_terminal(accession) for accession in ACCESSIONS
    ] == [True, True, False]
    second = _service(persistence, tmp_path).run(FIXTURE_ZIP, confirm=True)
    assert second.skipped_resume == 0  # successful members are not reopened
    assert second.filings_parsed == 1
    assert second.failures == 0


def test_unsupported_accession_is_terminal_on_resume(persistence, tmp_path):
    legacy = (
        b"<SEC-DOCUMENT>\n<DOCUMENT>\n<TYPE>4\n<TEXT>Paper filing</TEXT>\n</DOCUMENT>"
    )
    fetched = []
    first = _service(
        persistence, tmp_path, make_transport(filing_bytes=legacy, fetched=fetched)
    ).run(FIXTURE_ZIP, confirm=True, cancelled=lambda: len(fetched) >= 1)
    assert first.unsupported == 1
    assert first.failures == 0
    second = _service(persistence, tmp_path).run(FIXTURE_ZIP, confirm=True)
    assert second.skipped_resume == 1
    assert second.filings_parsed == 2
