"""Version-three data survives the input-normalization repair migration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.coverage import CoverageRepository, DateInterval
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.refresh import RefreshStateRepository
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    UsTradeRepository,
)
from insider_scanner.persistence.schema import congress_trades, us_trades
from insider_scanner.services.context import open_persistence
from insider_scanner.services.us import UsScanService
from tests.factories import congress_trade, us_trade

INTERVAL = DateInterval(date(2026, 1, 1), date(2026, 1, 31))
REFRESHED_AT = datetime(2026, 2, 1, tzinfo=UTC)


@pytest.fixture
def legacy_database(tmp_path):
    database_file = tmp_path / "legacy.sqlite3"
    engine = create_sqlite_engine(database_file)
    bootstrap_database(engine, migrations=MIGRATIONS[:3])
    try:
        yield engine, database_file
    finally:
        engine.dispose()


def _rows(engine, table):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(table)).mappings()]


def test_upgrade_preserves_trades_and_invalidates_only_affected_source_state(
    legacy_database,
):
    engine, _ = legacy_database
    UsTradeRepository(engine).upsert(
        [
            us_trade(ticker="AAPL", source="openinsider", filing_date=None),
            us_trade(ticker="MSFT", filing_date=None),
            us_trade(ticker="NVDA", source="openinsider"),
            us_trade(ticker="IBM", filing_date=None),
        ]
    )
    with engine.begin() as connection:
        connection.execute(
            us_trades.update()
            .where(us_trades.c.ticker == "MSFT")
            .values(source_provenance_json='["secform4", "openinsider"]')
        )
        connection.execute(
            us_trades.update()
            .where(us_trades.c.ticker == "IBM")
            .values(source_provenance_json="not-json")
        )
    coverage = CoverageRepository(engine)
    refresh = RefreshStateRepository(engine)
    identities = [
        ("us", "AAPL", "openinsider"),
        ("us", "MSFT", "openinsider"),
        ("us", "*", "openinsider"),
        ("us", "NVDA", "openinsider"),
        ("us", "IBM", "openinsider"),
        ("us", "AAPL", "secform4"),
        ("congress", "AAPL", "house"),
    ]
    for domain, identifier, source in identities:
        coverage.add(domain, identifier, source, INTERVAL)
        refresh.set(domain, identifier, source, "latest:200", REFRESHED_AT)
    before = _rows(engine, us_trades)

    bootstrap_database(engine)

    assert _rows(engine, us_trades) == before
    for index, (domain, identifier, source) in enumerate(identities):
        assert coverage.get(domain, identifier, source) == (
            () if index < 3 else (INTERVAL,)
        )
        assert refresh.get(domain, identifier, source, "latest:200") == (
            None if index < 3 else REFRESHED_AT
        )

    # Migration history prevents repeated invalidation after a successful scan.
    coverage.add("us", "AAPL", "openinsider", INTERVAL)
    refresh.set("us", "*", "openinsider", "latest:200", REFRESHED_AT)
    bootstrap_database(engine)
    assert coverage.get("us", "AAPL", "openinsider") == (INTERVAL,)
    assert refresh.get("us", "*", "openinsider", "latest:200") == REFRESHED_AT


def test_upgrade_without_missing_openinsider_dates_keeps_coverage(legacy_database):
    engine, _ = legacy_database
    UsTradeRepository(engine).upsert(
        [us_trade(source="openinsider"), us_trade(ticker="IBM", filing_date=None)]
    )
    coverage = CoverageRepository(engine)
    coverage.add("us", "*", "openinsider", INTERVAL)

    bootstrap_database(engine)

    assert coverage.get("us", "*", "openinsider") == (INTERVAL,)


@pytest.mark.parametrize(
    ("label", "initial", "expected"),
    [
        ("Spouse/DC Over $1,000,000", (0, 0), (1_000_000, 1_000_000)),
        ("$1,001 - $15,000", (0, 0), (1001, 15_000)),
        ("Spouse/DC Over $1,000,000", (10, 20), (10, 20)),
        ("Unknown", (0, 0), (0, 0)),
        ("$15,000 - $1,001", (0, 0), (0, 0)),
    ],
)
def test_upgrade_repairs_only_missing_amount_bounds_without_changing_identity(
    legacy_database, label, initial, expected
):
    engine, _ = legacy_database
    trade = congress_trade(
        trade_type="Other",
        amount_range=label,
        amount_low=initial[0],
        amount_high=initial[1],
    )
    CongressTradeRepository(engine).upsert([trade])
    before = _rows(engine, congress_trades)[0]

    bootstrap_database(engine)

    after = _rows(engine, congress_trades)[0]
    assert after == {**before, "amount_low": expected[0], "amount_high": expected[1]}
    # The raw transaction code was discarded; Other cannot be guessed as Sale.
    assert after["trade_type"] == "Other"


def test_upgrade_allows_bounded_rescan_to_fill_date_under_existing_key(legacy_database):
    engine, database_file = legacy_database
    old_trade = us_trade(source="openinsider", filing_date=None)
    UsTradeRepository(engine).upsert([old_trade])
    CoverageRepository(engine).add("us", "AAPL", "openinsider", INTERVAL)
    before = _rows(engine, us_trades)[0]
    calls = []

    def fetch(ticker, start, end, use_cache):
        calls.append((ticker, start, end, use_cache))
        return [replace(old_trade, filing_date=date(2026, 1, 7))]

    persistence = open_persistence(database_file)
    try:
        result = UsScanService(persistence, adapters={"openinsider": fetch}).scan(
            "AAPL",
            sources=("openinsider",),
            start_date=INTERVAL.start,
            end_date=INTERVAL.end,
        )
        assert [trade.filing_date for trade in result] == [date(2026, 1, 7)]
        assert calls == [("AAPL", INTERVAL.start, INTERVAL.end, True)]
        after = _rows(engine, us_trades)
        assert len(after) == 1
        assert after[0]["id"] == before["id"]
        assert after[0]["canonical_key"] == before["canonical_key"]
        assert persistence.coverage.get("us", "AAPL", "openinsider") == (INTERVAL,)
    finally:
        persistence.close()
