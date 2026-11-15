"""Additive read-index upgrades and pooled Unicode normalization."""

from contextlib import ExitStack

from sqlalchemy import select

from insider_scanner.persistence.bootstrap import bootstrap_database
from insider_scanner.persistence.engine import SQLITE_POOL_SIZE, create_sqlite_engine
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.repositories import UsTradeRepository
from insider_scanner.persistence.schema import schema_version, us_trades
from tests.factories import us_trade


def test_every_pooled_connection_has_deterministic_unicode_casefold(tmp_path):
    engine = create_sqlite_engine(tmp_path / "unicode.sqlite3")
    try:
        with ExitStack() as stack:
            connections = [
                stack.enter_context(engine.connect()) for _ in range(SQLITE_POOL_SIZE)
            ]
            for connection in connections:
                functions = connection.exec_driver_sql("PRAGMA function_list").all()
                assert any(
                    row[0] == "casefold" and row[5] & 2048 for row in functions
                ), "all connections need the deterministic casefold function"
                assert (
                    connection.exec_driver_sql(
                        "SELECT casefold('MÜLLER Straße')"
                    ).scalar_one()
                    == "müller strasse"
                )
    finally:
        engine.dispose()


def test_read_index_upgrade_preserves_rows_and_uses_ordered_indexes(tmp_path):
    engine = create_sqlite_engine(tmp_path / "upgrade.sqlite3")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:11])
        UsTradeRepository(engine).upsert([us_trade(insider_name="MÜLLER Straße")])
        with engine.connect() as connection:
            before = connection.execute(select(us_trades)).mappings().all()
        bootstrap_database(engine)
        bootstrap_database(engine)
        with engine.connect() as connection:
            assert connection.execute(select(us_trades)).mappings().all() == before
            assert connection.execute(select(schema_version.c.version)).all()[-1] == (
                12,
            )
            indexes = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
            assert {
                "ix_us_trades_feed_date",
                "ix_us_trades_feed_person",
                "ix_us_trades_feed_issuer",
                "ix_us_trades_latest",
                "ix_us_trades_accession",
                "ix_us_trades_cik_issuer",
            } <= indexes
            assert "ix_us_trades_source_filing_date" not in indexes
            queries = (
                ("ORDER BY trade_date DESC, filing_date DESC, id", "feed_date"),
                (
                    (
                        "WHERE ticker = 'AAPL' "
                        "ORDER BY trade_date DESC, filing_date DESC, id"
                    ),
                    "feed_issuer",
                ),
                (
                    (
                        "WHERE casefold(insider_name) = 'müller strasse' "
                        "ORDER BY trade_date DESC, filing_date DESC, id"
                    ),
                    "feed_person",
                ),
            )
            for clause, suffix in queries:
                plan = connection.exec_driver_sql(
                    f"EXPLAIN QUERY PLAN SELECT id FROM us_trades {clause} LIMIT 20"
                ).all()
                assert any(f"ix_us_trades_{suffix}" in row[3] for row in plan)
                assert not any("TEMP B-TREE" in row[3] for row in plan)
    finally:
        engine.dispose()
