"""Durable trigram indexes preserve literal, Unicode substring searches."""

import pytest
from sqlalchemy import delete, select, text, update

from insider_scanner.persistence.bootstrap import bootstrap_database
from insider_scanner.persistence.engine import create_sqlite_engine
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    EuropeanTradeRepository,
    UsTradeRepository,
)
from insider_scanner.persistence.schema import (
    congress_trades,
    european_trades,
    us_trades,
)
from insider_scanner.persistence.search import (
    install_search_indexes,
    search_index_condition,
)
from tests.factories import congress_trade, eu_trade, us_trade

_MARKETS = (
    (us_trades, UsTradeRepository, us_trade, "company"),
    (congress_trades, CongressTradeRepository, congress_trade, "asset_description"),
    (european_trades, EuropeanTradeRepository, eu_trade, "issuer_name"),
)


def _matching_ids(connection, source, search, *, fields=()):
    condition = search_index_condition(source, search, fields=fields)
    assert condition is not None
    return connection.execute(select(source.c.id).where(condition)).scalars().all()


def test_database_installs_trigram_search_for_every_market(engine):
    with engine.connect() as connection:
        names = (
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE sql LIKE '%USING fts5%'")
            )
            .scalars()
            .all()
        )

    assert sorted(names) == [
        "congress_trades_search",
        "european_trades_search",
        "us_trades_search",
    ]


@pytest.mark.parametrize("source,repository,factory,issuer", _MARKETS)
def test_installation_backfills_existing_rows_and_is_repeatable(
    tmp_path, source, repository, factory, issuer
):
    engine = create_sqlite_engine(tmp_path / "legacy-search.sqlite3")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:11])
        repository(engine).upsert([factory(**{issuer: "STRAßE MÜLLER"})])
        with engine.begin() as connection:
            original = connection.execute(select(source)).mappings().one()
            install_search_indexes(connection)
            install_search_indexes(connection)
            assert _matching_ids(connection, source, "strasse müller") == [
                original["id"]
            ]
            assert connection.execute(select(source)).mappings().one() == original
            count = connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {source.name}_search"
            ).scalar_one()
            assert count == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("source,repository,factory,issuer", _MARKETS)
def test_search_triggers_follow_insert_update_delete_and_rollback(
    engine, source, repository, factory, issuer
):
    repository(engine).upsert([factory(**{issuer: "Original Company"})])
    with engine.begin() as connection:
        row_id = connection.execute(select(source.c.id)).scalar_one()
        assert _matching_ids(connection, source, "original") == [row_id]
        connection.execute(update(source).values({issuer: "Replacement Company"}))
        assert _matching_ids(connection, source, "original") == []
        assert _matching_ids(connection, source, "replacement") == [row_id]

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(update(source).values({issuer: "Uncommitted Company"}))
        assert _matching_ids(connection, source, "uncommitted") == [row_id]
        transaction.rollback()
        assert _matching_ids(connection, source, "uncommitted") == []
        assert _matching_ids(connection, source, "replacement") == [row_id]

    with engine.begin() as connection:
        connection.execute(delete(source))
        assert _matching_ids(connection, source, "replacement") == []
        assert (
            connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {source.name}_search"
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    "needle",
    ["Straße", "MÜLLER", '"quoted"', "50%_profit", "foo OR bar", "a.b-c", "a b"],
)
def test_match_search_treats_unicode_and_query_operators_as_literal_text(
    engine, needle
):
    UsTradeRepository(engine).upsert(
        [
            us_trade(ticker="MATCH", company=f"prefix {needle} suffix"),
            us_trade(ticker="DECOY", company="prefix unrelated suffix"),
        ]
    )
    with engine.connect() as connection:
        matching = _matching_ids(connection, us_trades, needle.swapcase())
        assert (
            matching
            == connection.execute(
                select(us_trades.c.id).where(us_trades.c.ticker == "MATCH")
            )
            .scalars()
            .all()
        )


def test_match_column_filters_preserve_search_boundaries(engine):
    UsTradeRepository(engine).upsert([us_trade(company="Alpha", insider_name="Beta")])
    with engine.connect() as connection:
        row_id = connection.execute(select(us_trades.c.id)).scalar_one()
        assert _matching_ids(connection, us_trades, "alpha beta") == []
        assert _matching_ids(connection, us_trades, "alpha", fields=("person",)) == []
        assert _matching_ids(connection, us_trades, "beta", fields=("person",)) == [
            row_id
        ]
        assert _matching_ids(
            connection, us_trades, "alpha", fields=("issuer", "identifier")
        ) == [row_id]


def test_short_search_requires_literal_fallback_and_match_uses_virtual_index(engine):
    assert search_index_condition(us_trades, "ab") is None
    assert search_index_condition(us_trades, "ß") is None
    assert search_index_condition(us_trades, "ßx") is not None
    condition = search_index_condition(us_trades, "company")
    with engine.connect() as connection:
        statement = select(us_trades.c.id).where(condition)
        compiled = statement.compile(
            dialect=connection.dialect, compile_kwargs={"literal_binds": True}
        )
        plan = connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {compiled}").all()
    assert any("VIRTUAL TABLE INDEX" in row[3] and ":M" in row[3] for row in plan)


def test_search_index_rejects_unknown_columns():
    with pytest.raises(ValueError, match="search field"):
        search_index_condition(us_trades, "company", fields=("issuer : other",))
