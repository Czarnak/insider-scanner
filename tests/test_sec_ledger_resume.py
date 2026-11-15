"""Durable filing progress survives scale, interrupted runs and archive changes."""

from __future__ import annotations

from datetime import date
from math import ceil

import pytest
from sqlalchemy import event, func, select

from insider_scanner.core.sec_bulk import BulkFilingMetadata, SecBulkError
from insider_scanner.core.sec_ingestion import SecFilingOutcome, SecFilingStatus
from insider_scanner.persistence.schema import sec_ingested_filings, us_trades
from insider_scanner.services import sec_backfill, sec_persistence
from insider_scanner.services.context import open_persistence
from tests.factories import sec_trade
from tests.sec_stubs import make_client, make_transport


def _synthetic_archive(monkeypatch, count):
    """Keep real mapping/batch/database work while replacing remote filing I/O."""
    metadata = tuple(
        BulkFilingMetadata(
            cik="0000320193" if index % 2 == 0 else "0000789019",
            form_type="4",
            filing_date=date(2026, 1, 7),
            accession_number=f"0000320193-26-{index + 1:06d}",
            primary_document="ownership.xml",
        )
        for index in range(count)
    )
    fetched = []
    dispositions = {}

    def iterate(_archive, *, ciks=None, **_kwargs):
        for meta in metadata:
            if ciks is None or meta.cik in ciks:
                yield meta

    def process(row, *, counters, on_filing_parsed, **_kwargs):
        accession = row.archive_path.rsplit("/", 1)[-1].removesuffix(".txt")
        fetched.append(accession)
        status = dispositions.get(accession, SecFilingStatus.SUCCESS)
        if status is SecFilingStatus.SUCCESS:
            counters["parsed"] += 1
            on_filing_parsed(row, None)
            return SecFilingOutcome(accession, status)
        if status is SecFilingStatus.UNSUPPORTED:
            counters["unsupported"] += 1
        else:
            counters["failed"] += 1
        return SecFilingOutcome(accession, status, "parse", "synthetic_document")

    def map_trades(_filing, row):
        accession = row.archive_path.rsplit("/", 1)[-1].removesuffix(".txt")
        return [sec_trade(accession_number=accession)]

    monkeypatch.setattr(sec_backfill, "iter_ownership_filings", iterate)
    monkeypatch.setattr(
        sec_backfill, "bulk_archive_fingerprint", lambda *_a, **_k: "synthetic"
    )
    monkeypatch.setattr(sec_backfill, "process_filing_row", process)
    monkeypatch.setattr(sec_persistence, "ownership_filing_to_trades", map_trades)
    return metadata, fetched, dispositions


def _service(persistence, tmp_path, **kwargs):
    return sec_backfill.SecBackfillService(
        persistence,
        client=make_client(make_transport()),
        cache_root=tmp_path / "cache",
        cleanup=False,
        **kwargs,
    )


def _count(engine, table):
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table))


def test_large_backfill_batches_ledger_writes_and_publishes_committed_progress(
    persistence, tmp_path, monkeypatch
):
    total, batch_size = 5_001, 200
    metadata, fetched, _ = _synthetic_archive(monkeypatch, total)
    batches = []
    ledger_writes = []
    snapshots = []
    original = persistence.sec_ingestion.commit

    def commit(entries):
        batches.append(len(entries))
        return original(entries)

    def sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO sec_ingested_filings"):
            ledger_writes.append(1)

    def progress(snapshot):
        # Observed parse progress can precede the batch commit. Explicit
        # committed counts must always match durable rows and ledger entries.
        assert (
            _count(persistence.engine, sec_ingested_filings)
            == snapshot.filings_committed
        )
        assert _count(persistence.engine, us_trades) == snapshot.transactions_inserted
        assert snapshot.filings_committed <= snapshot.filings_parsed
        if snapshot.filings_committed:
            last = metadata[snapshot.filings_committed - 1].accession_number
            assert persistence.sec_ingestion.is_terminal(last)
        if snapshot.filings_committed and (
            not snapshots or snapshots[-1] != snapshot.filings_committed
        ):
            snapshots.append(snapshot.filings_committed)

    monkeypatch.setattr(persistence.sec_ingestion, "commit", commit)
    event.listen(persistence.engine, "before_cursor_execute", sql)
    try:
        summary = _service(persistence, tmp_path, batch_filings=batch_size).run(
            tmp_path / "synthetic.zip", confirm=True, on_progress=progress
        )
    finally:
        event.remove(persistence.engine, "before_cursor_execute", sql)

    assert batches == [batch_size] * (total // batch_size) + [total % batch_size]
    assert len(ledger_writes) == ceil(total / batch_size)
    assert snapshots == list(range(batch_size, total, batch_size)) + [total]
    assert len(fetched) == summary.filings_discovered == summary.filings_parsed == total
    assert summary.transactions_inserted == total
    assert summary.failures == summary.skipped_resume == 0


def test_cancelled_backfill_reopens_with_exact_committed_accessions_and_no_refetch(
    tmp_path, monkeypatch
):
    metadata, fetched, _ = _synthetic_archive(monkeypatch, 3)
    database = tmp_path / "resume.sqlite3"
    with_context = open_persistence(database)
    try:
        first = _service(with_context, tmp_path).run(
            tmp_path / "original.zip", confirm=True, cancelled=lambda: len(fetched) == 1
        )
        assert first.cancelled
        assert first.filings_discovered == first.filings_parsed == 1
        assert first.transactions_inserted == 1
        assert with_context.sec_ingestion.is_terminal(metadata[0].accession_number)
        assert with_context.sec_ingestion.get(metadata[1].accession_number) is None
        assert _count(with_context.engine, sec_ingested_filings) == 1
    finally:
        with_context.close()

    fetched.clear()
    reopened = open_persistence(database)
    try:
        second = _service(reopened, tmp_path).run(
            tmp_path / "renamed.zip", confirm=True
        )
        assert second.skipped_resume == 1
        assert second.filings_discovered == 3
        assert second.filings_parsed == second.transactions_inserted == 2
        assert second.failures == 0
        assert fetched == [meta.accession_number for meta in metadata[1:]]
        assert _count(reopened.engine, sec_ingested_filings) == 3
        assert _count(reopened.engine, us_trades) == 3
    finally:
        reopened.close()


def test_targeted_run_preserves_interrupted_full_run_across_archive_changes(
    persistence, tmp_path, monkeypatch
):
    metadata, fetched, _ = _synthetic_archive(monkeypatch, 3)
    first = _service(persistence, tmp_path).run(
        tmp_path / "full.zip", confirm=True, cancelled=lambda: len(fetched) == 1
    )
    assert first.cancelled and first.transactions_inserted == 1

    fetched.clear()
    targeted = _service(persistence, tmp_path).run(
        tmp_path / "full.zip", confirm=True, ciks=frozenset({"0000789019"})
    )
    assert targeted.filings_discovered == targeted.filings_parsed == 1
    assert fetched == [metadata[1].accession_number]

    fetched.clear()
    resumed = _service(persistence, tmp_path).run(
        tmp_path / "new-download.zip", confirm=True
    )
    assert resumed.skipped_resume == 2
    assert resumed.filings_parsed == resumed.transactions_inserted == 1
    assert fetched == [metadata[2].accession_number]
    assert _count(persistence.engine, us_trades) == 3
    assert [
        persistence.sec_ingestion.get(meta.accession_number).attempts
        for meta in metadata
    ] == [1, 1, 1]


def test_transient_retries_increment_attempts_while_terminal_rejections_stay_retired(
    persistence, tmp_path, monkeypatch
):
    metadata, fetched, dispositions = _synthetic_archive(monkeypatch, 4)
    accessions = [meta.accession_number for meta in metadata]
    dispositions.update(
        {
            accessions[0]: SecFilingStatus.RETRYABLE_FAILURE,
            accessions[1]: SecFilingStatus.PERMANENT_FAILURE,
            accessions[2]: SecFilingStatus.UNSUPPORTED,
        }
    )
    first = _service(persistence, tmp_path).run(tmp_path / "archive.zip", confirm=True)
    assert first.failures == 2
    assert first.unsupported == first.filings_parsed == first.transactions_inserted == 1
    for accession, status in dispositions.items():
        record = persistence.sec_ingestion.get(accession)
        assert (record.status, record.attempts, record.stage, record.reason) == (
            status,
            1,
            "parse",
            "synthetic_document",
        )

    dispositions[accessions[0]] = SecFilingStatus.SUCCESS
    fetched.clear()
    second = _service(persistence, tmp_path).run(tmp_path / "archive.zip", confirm=True)
    assert fetched == [accessions[0]]
    assert second.skipped_resume == 3
    assert second.filings_parsed == second.transactions_inserted == 1
    assert second.failures == second.unsupported == 0
    assert [
        persistence.sec_ingestion.get(accession).attempts for accession in accessions
    ] == [2, 1, 1, 1]
    assert _count(persistence.engine, us_trades) == 2


@pytest.mark.parametrize("abort", [KeyboardInterrupt, SecBulkError])
def test_archive_abort_flushes_pending_success_and_resume_never_refetches_it(
    persistence, tmp_path, monkeypatch, abort
):
    metadata, fetched, _ = _synthetic_archive(monkeypatch, 3)
    original = sec_backfill.iter_ownership_filings

    def interrupted(*args, **kwargs):
        iterator = original(*args, **kwargs)
        yield next(iterator)
        raise abort("interrupted archive")

    monkeypatch.setattr(sec_backfill, "iter_ownership_filings", interrupted)
    with pytest.raises(abort, match="interrupted archive"):
        _service(persistence, tmp_path).run(tmp_path / "archive.zip", confirm=True)

    assert fetched == [metadata[0].accession_number]
    assert _count(persistence.engine, us_trades) == 1
    assert persistence.sec_ingestion.is_terminal(metadata[0].accession_number)
    monkeypatch.setattr(sec_backfill, "iter_ownership_filings", original)
    fetched.clear()
    resumed = _service(persistence, tmp_path).run(
        tmp_path / "archive.zip", confirm=True
    )
    assert resumed.skipped_resume == 1
    assert resumed.filings_parsed == resumed.transactions_inserted == 2
    assert fetched == [meta.accession_number for meta in metadata[1:]]
    assert _count(persistence.engine, us_trades) == 3


@pytest.mark.parametrize(
    "legacy_state",
    [
        "{broken",
        '{"version": 999}',
        '{"version": 1, "zip": "archive.zip", "completed_accessions": ["0000320193-26-000001"]}',
    ],
)
def test_legacy_json_checkpoint_is_preserved_and_cannot_claim_uncommitted_data(
    persistence, tmp_path, monkeypatch, legacy_state
):
    _, fetched, _ = _synthetic_archive(monkeypatch, 1)
    checkpoint = tmp_path / "sec_backfill_checkpoint.json"
    checkpoint.write_text(legacy_state, encoding="utf-8")
    summary = _service(persistence, tmp_path).run(
        tmp_path / "archive.zip", confirm=True
    )
    assert summary.skipped_resume == 0
    assert summary.filings_parsed == summary.transactions_inserted == 1
    assert len(fetched) == 1
    assert checkpoint.read_text(encoding="utf-8") == legacy_state
