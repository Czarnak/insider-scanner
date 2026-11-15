from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine

from insider_scanner.core.prices import repository as repo
from insider_scanner.core.prices.model import PriceBar
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.persistence.schema import price_history, scan_coverage


@pytest.fixture
def e() -> Engine:
    engine = create_engine("sqlite://")  # in-memory
    price_history.create(engine)
    scan_coverage.create(engine)
    yield engine
    engine.dispose()


def test_bar_roundtrips_through_db(e):
    bar = PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 100, adjusted_close=1.4)
    repo.upsert_bars(e, [bar], source="stooq")
    out = repo.get_bars(e, "AAPL", date(2026, 1, 1), date(2026, 1, 31))
    assert len(out) == 1
    assert out[0].close == 1.5
    assert out[0].adjusted_close == 1.4


def test_bar_batch_uses_one_executemany_without_selecting_existing_rows(e):
    calls = []

    def record_call(connection, cursor, statement, parameters, context, executemany):
        calls.append((statement, executemany))

    event.listen(e, "before_cursor_execute", record_call)
    try:
        written = repo.upsert_bars(
            e,
            [
                PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 100),
                PriceBar("AAPL", date(2026, 1, 6), 2, 3, 1, 2.5, 200),
            ],
            source="yahoo",
        )
    finally:
        event.remove(e, "before_cursor_execute", record_call)

    assert written == 2
    assert len(calls) == 1
    assert calls[0][0].startswith("INSERT INTO price_history")
    assert "ON CONFLICT" in calls[0][0]
    assert calls[0][1] is True


def test_bar_batch_preserves_input_order_and_source_when_unspecified(e):
    original = PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 100)
    corrected = PriceBar("AAPL", original.date, 2, 3, 1, 2.5, 200, 2.4)
    repo.upsert_bars(e, [original], source="yahoo")

    assert repo.upsert_bars(e, [original, corrected]) == 2
    with e.connect() as connection:
        row = connection.execute(select(price_history)).mappings().one()
    assert row["close"] == 2.5
    assert row["volume"] == 200
    assert row["adjusted_close"] == 2.4
    assert row["source"] == "yahoo"

    repo.upsert_bars(e, [original], source="tiingo")
    with e.connect() as connection:
        row = connection.execute(select(price_history)).mappings().one()
    assert row["close"] == 1.5
    assert row["adjusted_close"] is None
    assert row["source"] == "tiingo"


def test_get_coverage_none_when_empty(e):
    assert repo.get_coverage(e, "AAPL") is None


def test_get_coverage_returns_min_max(e):
    repo.upsert_bars(
        e,
        [
            PriceBar("AAPL", date(2026, 1, 5), 1, 1, 1, 1, 1),
            PriceBar("AAPL", date(2026, 1, 9), 1, 1, 1, 1, 1),
        ],
    )
    assert repo.get_coverage(e, "AAPL") == (date(2026, 1, 5), date(2026, 1, 9))


@pytest.mark.parametrize("replace", [False, True])
def test_coverage_write_failure_rolls_back_bars_and_provider_invalidation(e, replace):
    now = datetime(2026, 11, 15, tzinfo=UTC)
    interval = DateInterval(date(2026, 1, 1), date(2026, 1, 4))
    original = PriceBar("AAPL", interval.start, 10, 10, 10, 10, 1)
    repo.store_fetch(e, "AAPL", "yahoo", interval, [original], now=now, complete=True)

    def fail_coverage(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO scan_coverage"):
            raise RuntimeError("simulated coverage storage failure")

    event.listen(e, "before_cursor_execute", fail_coverage)
    try:
        with pytest.raises(RuntimeError, match="coverage storage"):
            repo.store_fetch(
                e,
                "AAPL",
                "yahoo" if replace else "tiingo",
                interval,
                [PriceBar("AAPL", interval.start, 5, 5, 5, 5, 1)],
                now=now,
                complete=True,
                replace=replace,
            )
    finally:
        event.remove(e, "before_cursor_execute", fail_coverage)
    assert repo.get_bars(e, "AAPL", interval.start, interval.end) == [original]
    assert repo.fetched_intervals(e, "AAPL", "yahoo", now=now) == (interval,)
    assert repo.fetched_intervals(e, "AAPL", "tiingo", now=now) == ()
