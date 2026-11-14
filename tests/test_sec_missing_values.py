"""SEC numeric availability survives export and compatible SQLite upgrades."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select

from insider_scanner.core.models import InsiderTrade
from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.mappings import us_from_row, us_to_values
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.repositories import UsTradeRepository
from insider_scanner.persistence.schema import us_trades

_MISSING_FLAGS = (
    "shares_missing",
    "price_missing",
    "value_missing",
    "shares_owned_after_missing",
)


@pytest.mark.parametrize("flag", _MISSING_FLAGS)
def test_missing_flag_survives_json_and_row_mapping(flag):
    trade = InsiderTrade(ticker="TEST", source="sec_edgar", **{flag: True})

    assert InsiderTrade.from_dict(trade.to_dict()) == trade
    assert us_from_row(us_to_values(trade)) == trade
    assert getattr(trade, flag) is True


def test_legacy_exports_keep_reported_zero_defaults():
    trade = InsiderTrade.from_dict({"ticker": "TEST", "price": 0, "value": 0})

    assert trade.price == trade.value == 0
    assert all(getattr(trade, flag) is False for flag in _MISSING_FLAGS)


def test_missing_flags_survive_repository_round_trip(engine):
    trade = InsiderTrade(
        ticker="TEST",
        source="sec_edgar",
        accession_number="0000000001-26-000001",
        sec_row_id="nonDerivative:0",
        shares_missing=True,
        price_missing=True,
        value_missing=True,
        shares_owned_after_missing=True,
    )
    repository = UsTradeRepository(engine)
    repository.upsert([trade])

    assert repository.query("TEST") == [trade]


def test_version_five_upgrade_preserves_numbers_keys_and_unknown_legacy_zeros(tmp_path):
    engine = create_sqlite_engine(tmp_path / "legacy-v5.sqlite3")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:5])
        assert not set(_MISSING_FLAGS) & {
            column["name"] for column in inspect(engine).get_columns("us_trades")
        }
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO us_trades "
                "(canonical_key, ticker, trade_type, source, shares, price, value, "
                "shares_owned_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("zero", "ZERO", "Other", "sec_edgar", 0, 0, 0, 0),
                    ("known", "TEST", "Buy", "openinsider", 10, 2, 20, 30),
                ],
            )

        bootstrap_database(engine)
        bootstrap_database(engine)
        with engine.connect() as connection:
            rows = (
                connection.execute(select(us_trades).order_by(us_trades.c.id))
                .mappings()
                .all()
            )

        assert [row["id"] for row in rows] == [1, 2]
        assert [row["canonical_key"] for row in rows] == ["zero", "known"]
        assert [
            (row["shares"], row["price"], row["value"], row["shares_owned_after"])
            for row in rows
        ] == [(0, 0, 0, 0), (10, 2, 20, 30)]
        assert all(row[flag] is False for row in rows for flag in _MISSING_FLAGS)
    finally:
        engine.dispose()
