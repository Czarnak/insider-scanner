"""Committed SEC trades and filing progress must survive and fail together."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import OperationalError

from insider_scanner.core.sec_ingestion import SecFilingOutcome, SecFilingStatus
from insider_scanner.persistence.bootstrap import bootstrap_database
from insider_scanner.persistence.engine import create_sqlite_engine
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.repositories import UsTradeRepository
from insider_scanner.persistence.schema import schema_version
from insider_scanner.persistence.sec_ingestion import (
    SecIngestionRepository,
    SecIngestionWrite,
)
from insider_scanner.services.context import open_persistence
from tests.factories import sec_trade

ACCESSION = "0000320193-26-000001"


def successful_write(accession=ACCESSION, **overrides):
    return SecIngestionWrite(
        SecFilingOutcome(accession, SecFilingStatus.SUCCESS),
        (sec_trade(accession_number=accession, **overrides),),
    )


def test_commit_publishes_success_and_rows_together(persistence):
    result = persistence.sec_ingestion.commit([successful_write()])

    assert result.upserts.inserted == 1
    assert result.outcomes == (SecFilingOutcome(ACCESSION, SecFilingStatus.SUCCESS),)
    record = persistence.sec_ingestion.get(ACCESSION)
    assert record is not None
    assert record.status is SecFilingStatus.SUCCESS
    assert record.attempts == 1
    assert record.complete
    assert record.updated_at.tzinfo is not None
    assert persistence.sec_ingestion.is_terminal(ACCESSION)
    assert persistence.us_trades.query_latest(10) == [sec_trade()]


@pytest.mark.parametrize("status", list(SecFilingStatus))
def test_empty_filing_outcomes_remain_durable(persistence, status):
    outcome = SecFilingOutcome(ACCESSION, status, "parse", "reason\nwith control")
    result = persistence.sec_ingestion.commit([SecIngestionWrite(outcome)])
    record = persistence.sec_ingestion.get(ACCESSION)

    assert result.upserts.inserted == 0
    assert record is not None
    assert record.status is status
    assert record.stage == "parse"
    assert record.reason == "reason?with control"
    assert record.attempts == 1
    assert persistence.sec_ingestion.is_terminal(ACCESSION) == outcome.complete
    assert persistence.us_trades.query_latest(10) == []


def test_retry_attempts_increment_and_success_clears_diagnostics(persistence):
    retry = SecIngestionWrite(
        SecFilingOutcome(
            ACCESSION, SecFilingStatus.RETRYABLE_FAILURE, "fetch", "timeout"
        )
    )
    persistence.sec_ingestion.commit([retry])
    persistence.sec_ingestion.commit([retry])
    record = persistence.sec_ingestion.get(ACCESSION)
    assert record is not None
    assert record.attempts == 2
    assert not record.complete

    persistence.sec_ingestion.commit([successful_write()])
    record = persistence.sec_ingestion.get(ACCESSION)
    assert record is not None
    assert record.attempts == 3
    assert record.status is SecFilingStatus.SUCCESS
    assert record.stage == record.reason == ""
    assert len(persistence.us_trades.query_latest(10)) == 1


@pytest.mark.parametrize(
    "status",
    [
        SecFilingStatus.RETRYABLE_FAILURE,
        SecFilingStatus.PERMANENT_FAILURE,
        SecFilingStatus.UNSUPPORTED,
    ],
)
def test_overlapping_late_failure_cannot_downgrade_committed_success(
    persistence, status
):
    overlapping = SecIngestionRepository(persistence.engine)
    assert overlapping.get(ACCESSION) is None
    persistence.sec_ingestion.commit([successful_write()])

    # The other run started its fetch before the successful commit and only now
    # finishes its unsuccessful attempt. Keep that attempt's report truthful,
    # while the durable ledger retains the successfully committed result.
    result = overlapping.commit(
        [
            SecIngestionWrite(
                SecFilingOutcome(ACCESSION, status, "fetch", "late failure")
            )
        ]
    )

    assert result.outcomes[0].status is status
    record = persistence.sec_ingestion.get(ACCESSION)
    assert record is not None
    assert record.status is SecFilingStatus.SUCCESS
    assert record.attempts == 2
    assert record.stage == record.reason == ""
    assert persistence.us_trades.query_latest(10) == [sec_trade()]


@pytest.mark.parametrize("failure", ["database", "interrupt"])
def test_ledger_write_failure_rolls_back_all_trades_and_progress(persistence, failure):
    def fail_ledger(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO sec_ingested_filings"):
            if failure == "interrupt":
                raise KeyboardInterrupt
            raise OperationalError(
                "ledger insert", {}, sqlite3.OperationalError("simulated failure")
            )

    event.listen(persistence.engine, "before_cursor_execute", fail_ledger)
    error = KeyboardInterrupt if failure == "interrupt" else PersistenceError
    try:
        with pytest.raises(error):
            persistence.sec_ingestion.commit(
                [successful_write(), successful_write("0000320193-26-000002")]
            )
    finally:
        event.remove(persistence.engine, "before_cursor_execute", fail_ledger)

    assert persistence.us_trades.query_latest(10) == []
    assert persistence.sec_ingestion.get(ACCESSION) is None
    assert persistence.sec_ingestion.get("0000320193-26-000002") is None


def test_bad_row_commits_good_rows_but_keeps_only_its_filing_retryable(persistence):
    bad = SecIngestionWrite(
        SecFilingOutcome(ACCESSION, SecFilingStatus.SUCCESS),
        (sec_trade(price=-1), sec_trade(sec_row_id="non-derivative:1")),
    )
    second = "0000320193-26-000002"
    result = persistence.sec_ingestion.commit([bad, successful_write(second)])

    assert result.upserts.inserted == 2
    assert result.upserts.failed == 1
    assert [item.status for item in result.outcomes] == [
        SecFilingStatus.RETRYABLE_FAILURE,
        SecFilingStatus.SUCCESS,
    ]
    assert result.outcomes[0].stage == "persist"
    assert not persistence.sec_ingestion.is_terminal(ACCESSION)
    assert persistence.sec_ingestion.is_terminal(second)
    assert len(persistence.us_trades.query_latest(10)) == 2


def test_reopen_retains_progress_without_an_archive_identity(tmp_path):
    database = tmp_path / "ingestion.db"
    first = open_persistence(database)
    try:
        first.sec_ingestion.commit([successful_write()])
    finally:
        first.close()
    second = open_persistence(database)
    try:
        assert second.sec_ingestion.is_terminal(ACCESSION)
        assert second.sec_ingestion.get(ACCESSION).attempts == 1
        assert len(second.us_trades.query_latest(10)) == 1
    finally:
        second.close()


def test_migration_adds_ledger_without_guessing_legacy_completion(tmp_path):
    engine = create_sqlite_engine(tmp_path / "legacy.db")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:8])
        UsTradeRepository(engine).upsert([sec_trade()])
        assert "sec_ingested_filings" not in inspect(engine).get_table_names()

        bootstrap_database(engine)

        assert UsTradeRepository(engine).query_latest(10) == [sec_trade()]
        assert SecIngestionRepository(engine).get(ACCESSION) is None
        with engine.connect() as connection:
            assert list(
                connection.execute(
                    select(schema_version.c.version).order_by(schema_version.c.version)
                ).scalars()
            ) == [migration.version for migration in MIGRATIONS]
    finally:
        engine.dispose()


def test_invalid_entry_cannot_create_unowned_rows(persistence):
    with pytest.raises(ValueError, match="accession"):
        persistence.sec_ingestion.commit(
            [SecIngestionWrite(SecFilingOutcome(None, SecFilingStatus.SUCCESS))]
        )
    with pytest.raises(ValueError, match="successful"):
        persistence.sec_ingestion.commit(
            [
                SecIngestionWrite(
                    SecFilingOutcome(ACCESSION, SecFilingStatus.RETRYABLE_FAILURE),
                    (sec_trade(),),
                )
            ]
        )
    assert persistence.us_trades.query_latest(10) == []
