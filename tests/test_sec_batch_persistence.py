"""SEC batching must retain rows without publishing uncommitted progress."""

from __future__ import annotations

from dataclasses import replace

import pytest

from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.services.sec_backfill import SecBackfillService
from insider_scanner.services.sec_daily import (
    SEC_DAILY_IDENTIFIER,
    SecDailyIngestionService,
)
from insider_scanner.services.sec_persistence import SecTradeBatch
from tests.factories import sec_trade
from tests.sec_stubs import (
    DAY,
    FIXTURE_ZIP,
    INDEX_TWO_ROWS,
    VALID_FILING,
    make_client,
    make_transport,
)


def test_batch_buffers_with_filing_and_row_limits(persistence, monkeypatch):
    calls = []
    original = persistence.sec_ingestion.commit

    def commit(entries):
        calls.append(sum(len(entry.trades) for entry in entries))
        return original(entries)

    batch = SecTradeBatch(commit, max_filings=2, max_rows=3)
    batch.add("first", [sec_trade(sec_row_id="0"), sec_trade(sec_row_id="1")])
    assert calls == []
    assert batch.take_outcomes() == ()
    batch.add("second", [sec_trade(sec_row_id="2"), sec_trade(sec_row_id="3")])
    assert calls == [2]  # Flush before exceeding the row ceiling.
    first = batch.take_outcomes()
    assert [item.accession for item in first] == ["first"]
    assert all(item.complete for item in first)
    batch.add("third", [sec_trade(sec_row_id="4")])
    assert calls == [2, 3]
    assert batch.upserts.inserted == 5
    assert [item.accession for item in batch.take_outcomes()] == ["second", "third"]
    assert batch.take_outcomes(flush=True) == ()


def test_batch_failed_transaction_retries_every_filing():
    def fail(trades):
        raise PersistenceError("database is locked")

    batch = SecTradeBatch(fail)
    batch.add("first", [sec_trade()])
    batch.add("second", [sec_trade(sec_row_id="1")])
    outcomes = batch.take_outcomes(flush=True)
    assert len(outcomes) == 2
    assert all(not item.complete and "locked" in item.reason for item in outcomes)
    assert batch.upserts.inserted == 0


def test_bad_row_keeps_its_filing_retryable_across_flushes(persistence):
    batch = SecTradeBatch(persistence.sec_ingestion.commit, max_filings=1)
    batch.add("first", [sec_trade(sec_row_id="0", price=-1), sec_trade(sec_row_id="1")])
    batch.add(
        "second", [sec_trade(sec_row_id="2", price=-1), sec_trade(sec_row_id="3")]
    )
    outcomes = batch.take_outcomes(flush=True)
    assert [item.accession for item in outcomes] == ["first", "second"]
    assert all(not item.complete and item.reason for item in outcomes)
    assert batch.upserts.inserted == 2
    assert {trade.sec_row_id for trade in persistence.us_trades.query_latest(10)} == {
        "1",
        "3",
    }


@pytest.mark.parametrize("kind", ["daily", "backfill"])
def test_services_commit_batches_before_progress(
    persistence, tmp_path, monkeypatch, kind
):
    calls = []
    original = persistence.sec_ingestion.commit

    def commit(entries):
        calls.append(sum(len(entry.trades) for entry in entries))
        # Coverage and ledger cannot claim an uncommitted batch.
        assert persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar") == ()
        assert all(
            persistence.sec_ingestion.get(entry.outcome.accession) is None
            for entry in entries
        )
        return original(entries)

    monkeypatch.setattr(persistence.sec_ingestion, "commit", commit)
    if kind == "daily":
        service = SecDailyIngestionService(
            persistence,
            client=make_client(make_transport(index_text=INDEX_TWO_ROWS)),
            cache_root=tmp_path / "cache",
        )
        snapshots = []

        def progress(snapshot):
            assert len(persistence.us_trades.query_latest(10)) == 2
            snapshots.append(snapshot)

        summary = service.ingest_date(DAY, on_progress=progress)
        assert summary.dates_completed == 1
        assert len(snapshots) == 1
        assert calls == [2]
    else:
        service = SecBackfillService(
            persistence,
            client=make_client(
                make_transport(
                    filing_bytes=VALID_FILING.replace(b"2026-06-12", b"2024-01-01")
                )
            ),
            cache_root=tmp_path / "cache",
        )
        summary = service.run(FIXTURE_ZIP, confirm=True)
        assert calls == [3]
    assert summary.transactions_inserted == sum(calls)
    assert summary.failures == 0


@pytest.mark.parametrize("kind", ["daily", "backfill"])
def test_row_rejection_retries_its_filing_and_retains_good_rows(
    persistence, tmp_path, monkeypatch, kind
):
    module = "insider_scanner.services.sec_persistence"
    from insider_scanner.core.sec_trade_mapping import ownership_filing_to_trades

    first_accession = None

    def map_with_bad_row(filing, row):
        nonlocal first_accession
        trades = list(ownership_filing_to_trades(filing, row))
        if first_accession is None:
            first_accession = trades[0].accession_number
            trades[0] = replace(trades[0], price=-1)
        return trades

    monkeypatch.setattr(f"{module}.ownership_filing_to_trades", map_with_bad_row)
    checkpoint = tmp_path / "checkpoint.json"
    if kind == "daily":
        service = SecDailyIngestionService(
            persistence,
            client=make_client(make_transport(index_text=INDEX_TWO_ROWS)),
            cache_root=tmp_path / "cache",
        )
        summary = service.ingest_date(DAY)
        assert summary.dates_completed == 0
        assert persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar") == ()
        assert not checkpoint.exists()
        assert summary.transactions_inserted == 1
    else:
        service = SecBackfillService(
            persistence,
            client=make_client(
                make_transport(
                    filing_bytes=VALID_FILING.replace(b"2026-06-12", b"2024-01-01")
                )
            ),
            cache_root=tmp_path / "cache",
        )
        summary = service.run(FIXTURE_ZIP, confirm=True)
        assert not persistence.sec_ingestion.is_terminal(first_accession)
        assert persistence.sec_ingestion.get(first_accession).stage == "persist"
        assert summary.transactions_inserted == 2
    assert summary.failures == 1
    assert summary.outcomes[0].stage == "persist"
    assert summary.outcomes[0].accession == first_accession
    assert len(persistence.us_trades.query_latest(10)) == summary.transactions_inserted
