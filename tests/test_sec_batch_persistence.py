"""SEC batching must retain rows without publishing uncommitted progress."""

from __future__ import annotations

import json
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
    original = persistence.us_trades.upsert

    def upsert(trades):
        rows = list(trades)
        calls.append(len(rows))
        return original(rows)

    batch = SecTradeBatch(upsert, max_filings=2, max_rows=3)
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


@pytest.mark.parametrize("kind", ["daily", "backfill"])
def test_services_commit_batches_before_progress(
    persistence, tmp_path, monkeypatch, kind
):
    calls = []
    original = persistence.us_trades.upsert

    def upsert(trades):
        rows = list(trades)
        calls.append(len(rows))
        # Neither coverage nor checkpoint may claim this batch yet.
        assert persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar") == ()
        assert not (tmp_path / "checkpoint.json").exists()
        return original(rows)

    monkeypatch.setattr(persistence.us_trades, "upsert", upsert)
    if kind == "daily":
        service = SecDailyIngestionService(
            persistence,
            client=make_client(make_transport(index_text=INDEX_TWO_ROWS)),
            cache_root=tmp_path / "cache",
            checkpoint_path=tmp_path / "checkpoint.json",
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
            checkpoint_path=tmp_path / "checkpoint.json",
        )
        summary = service.run(FIXTURE_ZIP, confirm=True)
        assert calls == [3]
    assert summary.transactions_inserted == sum(calls)
    assert summary.failures == 0


@pytest.mark.parametrize("kind", ["daily", "backfill"])
def test_row_rejection_retries_its_filing_and_retains_good_rows(
    persistence, tmp_path, monkeypatch, kind
):
    module = (
        f"insider_scanner.services.sec_{'daily' if kind == 'daily' else 'backfill'}"
    )
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
            checkpoint_path=checkpoint,
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
            checkpoint_path=checkpoint,
        )
        summary = service.run(FIXTURE_ZIP, confirm=True)
        completed = json.loads(checkpoint.read_text())["completed_accessions"]
        assert len(completed) == 2
        assert first_accession not in completed
        assert summary.transactions_inserted == 2
    assert summary.failures == 1
    assert summary.outcomes[0].stage == "persist"
    assert summary.outcomes[0].accession == first_accession
    assert len(persistence.us_trades.query_latest(10)) == summary.transactions_inserted
