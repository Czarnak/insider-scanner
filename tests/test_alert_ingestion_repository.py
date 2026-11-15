"""Alerts traverse committed ingestion order rather than transaction dates."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, insert, inspect, select

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.feed import FeedCriteria, FeedMarket, FeedRepository
from insider_scanner.persistence.mappings import (
    congress_to_values,
    european_to_values,
    us_to_values,
)
from insider_scanner.persistence.migrations import MIGRATIONS, Migration
from insider_scanner.persistence.repositories import UsTradeRepository
from insider_scanner.persistence.schema import (
    congress_trades,
    european_trades,
    schema_version,
    trade_replacements,
    us_trades,
)
from insider_scanner.persistence.watchlists import WatchEntry
from tests.factories import congress_trade, eu_trade, us_trade

_TABLES = (us_trades, congress_trades, european_trades)
_VALUES = (us_to_values, congress_to_values, european_to_values)
_FACTORIES = (us_trade, congress_trade, eu_trade)
_CREATED = datetime(2026, 1, 1, tzinfo=UTC)


def _insert(engine, market_index, key, **overrides):
    values = _VALUES[market_index](_FACTORIES[market_index]())
    values.update(canonical_key=key, created_at=_CREATED)
    values.update(overrides)
    with engine.begin() as connection:
        return connection.execute(
            insert(_TABLES[market_index]).values(values)
        ).inserted_primary_key[0]


def _query(engine, criteria=None, **kwargs):
    repository = FeedRepository(engine)
    query = getattr(repository, "query_alerts", None)
    assert callable(query), "Alerts need a bounded SQL query in ingestion order"
    return query(criteria or FeedCriteria(), **kwargs)


def test_new_old_dated_rows_are_filtered_before_the_alert_cap(engine):
    for i in range(205):
        _insert(engine, 0, f"existing-{i}", trade_date=date(2026, 1, 1))
    first = _query(engine, limit=200)
    assert first.total_count == 205
    assert first.has_more
    watermark = first.records[-1].ingestion_sequence
    old_id = _insert(engine, 0, "late-old", trade_date=date(2003, 1, 1))
    second = _query(engine, after_sequence=watermark, limit=200)
    assert second.total_count == 6
    assert second.records[-1].key == f"us:{old_id}"
    assert not second.has_more
    assert not _query(
        engine, after_sequence=second.records[-1].ingestion_sequence
    ).records


def test_ingestion_order_is_global_and_does_not_depend_on_equal_timestamps(engine):
    ids = [_insert(engine, index, f"market-{index}") for index in (2, 0, 1)]
    page = _query(engine, limit=2)
    assert [r.key for r in page.records] == [f"europe:{ids[0]}", f"us:{ids[1]}"]
    assert page.total_count == 3
    second = _query(engine, after_sequence=page.records[-1].ingestion_sequence)
    assert [r.key for r in second.records] == [f"congress:{ids[2]}"]


def test_new_rows_in_the_same_second_survive_acknowledgement(engine):
    _insert(engine, 0, "first")
    before = _query(engine)
    _insert(engine, 0, "second")
    after = _query(engine, after_sequence=before.records[0].ingestion_sequence)
    assert len(after.records) == 1
    assert after.records[0].created_at == before.records[0].created_at
    assert after.records[0].ingestion_sequence > before.records[0].ingestion_sequence


def test_legacy_timestamp_transition_rechecks_the_incomplete_second(engine):
    _insert(
        engine, 0, "before", created_at=datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
    )
    _insert(engine, 0, "same-second")
    page = _query(engine, created_after=_CREATED.replace(microsecond=999999))
    assert [r.key for r in page.records] == ["us:2"]


def test_legacy_transition_includes_server_default_timestamp_precision(engine):
    _insert(engine, 0, "whole-second")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE us_trades SET created_at = '2026-01-01 00:00:00'"
        )
    page = _query(engine, created_after=_CREATED.replace(microsecond=999999))
    assert [r.key for r in page.records] == ["us:1"]


def test_watchlist_candidates_are_exact_market_scoped_and_deduplicated(engine):
    _insert(engine, 0, "aapl")
    _insert(engine, 1, "congress-aapl", ticker="AAPL")
    _insert(engine, 0, "longer", ticker="AAPLX")
    _insert(engine, 0, "nonmatch", ticker="AAPLX", insider_name="Other Person")
    entries = (
        WatchEntry(kind="company", market=FeedMarket.US, identifier="aapl"),
        WatchEntry(kind="insider", market=FeedMarket.US, person="TIM COOK"),
    )
    page = _query(engine, watch_entries=entries)
    assert [r.key for r in page.records] == ["us:1", "us:2"]
    assert page.total_count == 2
    assert _query(engine, watch_entries=()).records == ()


def test_criteria_filters_apply_before_ingestion_pagination(engine):
    for i in range(205):
        _insert(engine, 0, f"small-{i}", value=1)
    _insert(engine, 0, "large", value=50000)
    page = _query(engine, FeedCriteria(value_min=40000), limit=1)
    assert [r.key for r in page.records] == ["us:206"]
    assert page.total_count == 1
    assert not page.has_more


def test_maximum_watchlist_does_not_exceed_sqlite_expression_depth(engine):
    _insert(engine, 0, "match", ticker="LAST")
    entries = tuple(
        WatchEntry(kind="company", market=FeedMarket.US, identifier=f"OTHER{i}")
        for i in range(999)
    ) + (WatchEntry(kind="company", market=FeedMarket.US, identifier="LAST"),)
    page = _query(engine, watch_entries=entries)
    assert [r.key for r in page.records] == ["us:1"]


def test_noop_upsert_does_not_make_an_acknowledged_trade_new(engine):
    repository = UsTradeRepository(engine)
    trade = us_trade()
    repository.upsert([trade])
    page = _query(engine)
    repository.upsert([trade])
    assert (
        _query(engine, after_sequence=page.records[0].ingestion_sequence).records == ()
    )


def test_deleted_maximum_row_id_reuse_gets_a_new_ingestion_sequence(engine):
    old_id = _insert(engine, 0, "first")
    before = _query(engine).records[0].ingestion_sequence
    with engine.begin() as connection:
        connection.execute(delete(us_trades))
    new_id = _insert(engine, 0, "replacement")
    assert new_id == old_id
    after = _query(engine, after_sequence=before)
    assert [r.key for r in after.records] == [f"us:{old_id}"]
    assert after.records[0].ingestion_sequence > before


def test_ingestion_mapping_rolls_back_with_source_rows(engine):
    _insert(engine, 0, "before")
    with engine.connect() as connection:
        transaction = connection.begin()
        values = us_to_values(us_trade(ticker="OTHER"))
        connection.execute(insert(us_trades).values(values))
        transaction.rollback()
    _insert(engine, 1, "after")
    assert [r.key for r in _query(engine).records] == ["us:1", "congress:1"]


def test_superseded_observations_are_excluded_before_alert_counting(engine):
    _insert(engine, 0, "old")
    _insert(engine, 0, "replacement")
    with engine.begin() as connection:
        connection.execute(
            insert(trade_replacements).values(
                market="us",
                canonical_key="old",
                replacement_key="replacement",
                reason="superseded",
            )
        )
    page = _query(engine)
    assert page.total_count == 1
    assert [r.key for r in page.records] == ["us:2"]


def test_failed_ingestion_migration_rolls_back_table_triggers_and_history(tmp_path):
    engine = create_sqlite_engine(tmp_path / "migration-failure.sqlite3")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:12])
        _insert(engine, 0, "retained")

        def fail_after_install(connection):
            MIGRATIONS[12].upgrade(connection)
            raise RuntimeError("Interrupted upgrade")

        failing = (
            *MIGRATIONS[:12],
            Migration(13, "failing_ingestion", fail_after_install),
        )
        with pytest.raises(PersistenceError, match="migration 13"):
            bootstrap_database(engine, migrations=failing)
        with engine.connect() as connection:
            assert "feed_ingestion" not in inspect(connection).get_table_names()
            assert (
                max(connection.execute(select(schema_version.c.version)).scalars())
                == 12
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE '%_ingestion_%'"
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(select(us_trades.c.canonical_key)).scalar_one()
                == "retained"
            )
        bootstrap_database(engine)
        assert [r.key for r in _query(engine).records] == ["us:1"]
    finally:
        engine.dispose()


def test_upgrade_from_v12_preserves_source_rows_and_backfills_all_markets(tmp_path):
    engine = create_sqlite_engine(tmp_path / "old.sqlite3")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:12])
        for index in range(3):
            _insert(engine, index, f"existing-{index}")
        with engine.connect() as connection:
            before = [
                dict(connection.execute(select(t)).mappings().one()) for t in _TABLES
            ]
        bootstrap_database(engine)
        assert len(_query(engine).records) == 3
        with engine.connect() as connection:
            after = [
                dict(connection.execute(select(t)).mappings().one()) for t in _TABLES
            ]
        assert before == after
        bootstrap_database(engine)
        assert len(_query(engine).records) == 3
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "kwargs", [{"limit": 0}, {"after_sequence": -1}, {"after_sequence": True}]
)
def test_alert_query_rejects_invalid_page_controls(engine, kwargs):
    with pytest.raises(ValueError):
        _query(engine, **kwargs)
