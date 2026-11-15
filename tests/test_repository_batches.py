"""Batch SQL, partial-row outcomes, and transaction regression coverage."""

import sqlite3
from dataclasses import replace

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from insider_scanner.persistence import repositories
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    EuropeanTradeRepository,
    UpsertResult,
    UsTradeRepository,
)
from tests.factories import congress_trade, eu_trade, sec_trade, us_trade


@pytest.mark.parametrize(
    "repository_type,factory,table",
    [
        (UsTradeRepository, us_trade, "us_trades"),
        (CongressTradeRepository, congress_trade, "congress_trades"),
        (EuropeanTradeRepository, eu_trade, "european_trades"),
    ],
)
def test_batch_prefetches_and_writes_in_chunks(engine, repository_type, factory, table):
    count = repositories.UPSERT_BATCH_SIZE * 2 + 1
    if factory is congress_trade:
        rows = [
            factory(doc_id=f"doc-{index}", source_row_id="1") for index in range(count)
        ]
        corrected = [replace(row, comment="Corrected") for row in rows]
    elif factory is eu_trade:
        rows = [factory(insider_name=f"Insider {index}") for index in range(count)]
        corrected = [replace(row, position="", source_url="") for row in rows]
        rows, corrected = corrected, rows
    else:
        rows = [factory(insider_name=f"Insider {index}") for index in range(count)]
        corrected = [replace(row, insider_title="CFO") for row in rows]
    calls = []

    def observe(_connection, _cursor, statement, _parameters, _context, executemany):
        if table in statement:
            calls.append((statement.lstrip().split()[0].upper(), executemany))

    event.listen(engine, "before_cursor_execute", observe)
    try:
        repository = repository_type(engine)
        # One additional scope read reconciles effective replacement links;
        # raw prefetch and writes remain batched rather than per input row.
        expected_selects = 3 if factory is eu_trade else 4
        assert repository.upsert(iter(rows)) == UpsertResult(inserted=count)
        assert sum(kind == "SELECT" for kind, _ in calls) == expected_selects
        assert sum(kind == "INSERT" for kind, _ in calls) == 3
        assert any(kind == "INSERT" and many for kind, many in calls)
        calls.clear()
        assert repository.upsert(corrected) == UpsertResult(updated=count)
        assert sum(kind == "SELECT" for kind, _ in calls) == expected_selects
        assert sum(kind == "UPDATE" for kind, _ in calls) == 3
        assert any(kind == "UPDATE" and many for kind, many in calls)
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def test_duplicate_sec_reparse_cannot_conceal_rejected_input(engine):
    valid = sec_trade()
    invalid = replace(valid, price=-1)
    repository = UsTradeRepository(engine)
    result = repository.upsert([invalid, valid, replace(valid, price=201)])
    assert (result.inserted, result.updated, result.failed) == (1, 1, 1)
    assert result.errors[0].index == 0
    assert "price" in result.errors[0].message
    assert repository.query(valid.ticker) == [replace(valid, price=201)]


def test_failed_executemany_rolls_back_prefix_before_individual_retry(engine):
    # A database constraint unavailable to Python prevalidation exercises the
    # actual SQL savepoint fallback, including an already-written valid prefix.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_bad_company BEFORE INSERT ON us_trades "
            "WHEN NEW.company = 'Rejected' BEGIN "
            "SELECT RAISE(ABORT, 'company policy failed'); END"
        )
    repository = UsTradeRepository(engine)
    rows = [us_trade(insider_name=f"Insider {index}") for index in range(3)]
    rows[1] = replace(rows[1], company="Rejected")
    result = repository.upsert(rows)
    assert (result.inserted, result.updated, result.skipped, result.failed) == (
        2,
        0,
        0,
        1,
    )
    assert result.errors[0].index == 1
    assert "company policy failed" in result.errors[0].message
    assert "INSERT INTO" not in result.errors[0].message
    assert {trade.insider_name for trade in repository.query("AAPL")} == {
        "Insider 0",
        "Insider 2",
    }


def test_row_error_indexes_span_batch_boundaries_and_results_add(engine, monkeypatch):
    monkeypatch.setattr(repositories, "UPSERT_BATCH_SIZE", 2)
    result = UsTradeRepository(engine).upsert(
        [
            us_trade(insider_name="First"),
            us_trade(insider_name="Bad one", price=-1),
            us_trade(insider_name="Second"),
            us_trade(insider_name="Bad two", company={"unbound": "value"}),
        ]
    )
    assert result.inserted == 2
    assert [error.index for error in result.errors] == [1, 3]
    assert (UpsertResult() + result).errors == result.errors


def test_operational_failure_rolls_back_prior_chunks_and_excludes_parameters(
    engine, monkeypatch
):
    monkeypatch.setattr(repositories, "UPSERT_BATCH_SIZE", 2)
    inserts = 0

    def fail_second_write(
        _connection, _cursor, statement, _parameters, _context, _many
    ):
        nonlocal inserts
        if statement.startswith("INSERT INTO us_trades"):
            inserts += 1
            if inserts == 2:
                cause = sqlite3.OperationalError("database is locked")
                cause.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise OperationalError(statement, {"sensitive": "not-for-logs"}, cause)

    event.listen(engine, "before_cursor_execute", fail_second_write)
    repository = UsTradeRepository(engine)
    try:
        with pytest.raises(PersistenceError) as error:
            repository.upsert([us_trade(insider_name=str(index)) for index in range(3)])
    finally:
        event.remove(engine, "before_cursor_execute", fail_second_write)
    assert error.value.retryable
    assert "database is locked" in str(error.value)
    assert "not-for-logs" not in str(error.value)
    assert "INSERT INTO" not in str(error.value)
    assert repository.query("AAPL") == []


def test_congress_promotion_within_batch_retains_lots_and_ignores_old_export(engine):
    legacy = congress_trade()
    native = replace(legacy, source_row_id="1")
    second_lot = replace(legacy, source_row_id="2")
    repository = CongressTradeRepository(engine)
    assert repository.upsert([legacy, native, second_lot, legacy]) == UpsertResult(
        inserted=2, updated=1, skipped=1
    )
    assert {row.source_row_id for row in repository.query()} == {"1", "2"}


@pytest.mark.parametrize(
    "repository_type,trade",
    [
        (UsTradeRepository, us_trade()),
        (CongressTradeRepository, congress_trade()),
        (EuropeanTradeRepository, eu_trade()),
    ],
)
def test_all_trade_batches_reserve_writer_before_first_read(
    engine, repository_type, trade
):
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        repository_type(engine).upsert([trade])
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert statements[0] == "BEGIN IMMEDIATE"
    assert (
        next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("SELECT")
        )
        > 0
    )
