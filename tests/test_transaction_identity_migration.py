"""Legacy raw observations survive the transaction identity upgrade."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import MetaData, Table, insert, inspect, select

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.coverage import CoverageRepository, DateInterval
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.mappings import (
    congress_to_values,
    us_canonical_key,
    us_to_values,
)
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.refresh import RefreshStateRepository
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    UpsertResult,
    UsTradeRepository,
)
from insider_scanner.persistence.schema import (
    congress_trades,
    schema_version,
    us_trades,
)
from tests.factories import congress_trade, sec_trade, us_trade

CREATED = datetime(2025, 1, 1, tzinfo=UTC)
UPDATED = datetime(2025, 2, 1, tzinfo=UTC)
INTERVAL = DateInterval(date(2025, 1, 1), date(2025, 1, 31))


@pytest.fixture
def version_six(tmp_path):
    engine = create_sqlite_engine(tmp_path / "version-six.sqlite3")
    bootstrap_database(engine, migrations=MIGRATIONS[:6])
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_historical(engine, name, values):
    table = Table(name, MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            insert(table),
            [
                {key: value for key, value in row.items() if key in table.c}
                for row in values
            ],
        )


def _rows(engine, table):
    with engine.connect() as connection:
        return list(connection.execute(select(table).order_by(table.c.id)).mappings())


def test_upgrade_rekeys_scraper_preserves_native_sec_and_reingest_is_idempotent(
    version_six,
):
    engine = version_six
    scraper = us_trade()
    native = sec_trade(shares=scraper.shares, value=scraper.value)
    _insert_historical(
        engine,
        "us_trades",
        [
            {
                **us_to_values(scraper),
                "canonical_key": "legacy-bucket",
                "created_at": CREATED,
                "updated_at": UPDATED,
            },
            {**us_to_values(native), "created_at": CREATED, "updated_at": UPDATED},
        ],
    )
    before = _rows(engine, us_trades)

    bootstrap_database(engine)

    after = _rows(engine, us_trades)
    assert dict(after[0]) == {
        **dict(before[0]),
        "canonical_key": us_canonical_key(scraper),
    }
    assert dict(after[1]) == dict(before[1])
    repo = UsTradeRepository(engine)
    assert repo.upsert([scraper, native]) == UpsertResult(skipped=2)
    assert len(repo.query("AAPL")) == 2
    bootstrap_database(engine)
    assert _rows(engine, us_trades) == after


def test_upgrade_coalesces_collisions_without_losing_provenance_or_timestamps(
    version_six,
):
    engine = version_six
    first = us_trade(company="", insider_title="Chief Executive")
    second = us_trade(
        source="openinsider", insider_title="", edgar_url="https://sec.test/report"
    )
    _insert_historical(
        engine,
        "us_trades",
        [
            {
                **us_to_values(first, {"edgar"}),
                "canonical_key": "legacy-a",
                "created_at": CREATED,
                "updated_at": CREATED,
            },
            {
                **us_to_values(second),
                "canonical_key": "legacy-b",
                "created_at": UPDATED,
                "updated_at": UPDATED,
            },
            {
                **us_to_values(us_trade(ticker="MSFT")),
                "canonical_key": "legacy-c",
                "created_at": CREATED,
                "updated_at": UPDATED,
            },
        ],
    )

    bootstrap_database(engine)

    rows = _rows(engine, us_trades)
    assert [row["id"] for row in rows] == [1, 3]
    merged = rows[0]
    assert merged["canonical_key"] == us_canonical_key(first)
    assert merged["company"] == "Apple Inc."
    assert merged["insider_title"] == "Chief Executive"
    assert merged["edgar_url"] == "https://sec.test/report"
    assert set(json.loads(merged["source_provenance_json"])) == {
        "edgar",
        "secform4",
        "openinsider",
    }
    assert merged["created_at"] == CREATED
    assert merged["updated_at"] == UPDATED
    assert rows[1]["canonical_key"] == us_canonical_key(us_trade(ticker="MSFT"))


def test_upgrade_invalidates_only_legacy_us_and_congress_fetch_state_once(version_six):
    engine = version_six
    identities = (
        ("us", "AAPL", "openinsider"),
        ("us", "*", "secform4"),
        ("congress", "Jane Doe", "house"),
        ("congress", "*", "senate"),
        ("us", "AAPL", "sec_edgar"),
        ("eu", "NL0000009165", "afm"),
    )
    coverage = CoverageRepository(engine)
    refresh = RefreshStateRepository(engine)
    for domain, identifier, source in identities:
        coverage.add(domain, identifier, source, INTERVAL)
        refresh.set(domain, identifier, source, "latest:200", UPDATED)

    bootstrap_database(engine)

    for index, (domain, identifier, source) in enumerate(identities):
        assert coverage.get(domain, identifier, source) == (
            () if index < 4 else (INTERVAL,)
        )
        assert refresh.get(domain, identifier, source, "latest:200") == (
            None if index < 4 else UPDATED
        )
    coverage.add("congress", "Jane Doe", "house", INTERVAL)
    refresh.set("us", "*", "secform4", "latest:200", UPDATED)
    bootstrap_database(engine)
    assert coverage.get("congress", "Jane Doe", "house") == (INTERVAL,)
    assert refresh.get("us", "*", "secform4", "latest:200") == UPDATED


def test_legacy_congress_observation_promotes_once_then_preserves_repeated_lots(
    version_six,
):
    engine = version_six
    legacy = congress_trade(comment="Z old description")
    _insert_historical(
        engine,
        "congress_trades",
        [{**congress_to_values(legacy), "created_at": CREATED, "updated_at": UPDATED}],
    )
    bootstrap_database(engine)
    before = _rows(engine, congress_trades)
    assert before[0]["source_row_id"] == ""
    first = replace(legacy, source_row_id="1", comment="A corrected description")
    second = replace(first, source_row_id="2")
    repo = CongressTradeRepository(engine)

    assert repo.upsert([first, second]) == UpsertResult(inserted=1, updated=1)
    assert repo.upsert([first, second]) == UpsertResult(skipped=2)
    after = _rows(engine, congress_trades)
    assert [row["source_row_id"] for row in after] == ["1", "2"]
    assert after[0]["id"] == before[0]["id"]
    assert after[0]["created_at"] == before[0]["created_at"]
    assert {row["comment"] for row in after} == {"A corrected description"}
    assert len(repo.query("Jane Doe")) == 2


def test_failed_upgrade_rolls_back_keys_row_column_and_schema_history(
    version_six, monkeypatch
):
    engine = version_six
    _insert_historical(
        engine,
        "us_trades",
        [
            {**us_to_values(us_trade()), "canonical_key": "legacy-a"},
            {
                **us_to_values(us_trade(source="openinsider")),
                "canonical_key": "legacy-b",
            },
        ],
    )
    before = _rows(engine, us_trades)

    def fail_merge(*_args):
        raise RuntimeError("injected collision merge failure")

    monkeypatch.setattr(
        "insider_scanner.persistence.migrations.merge_trade_pair", fail_merge
    )
    with pytest.raises(PersistenceError, match="migration 7"):
        bootstrap_database(engine)

    assert _rows(engine, us_trades) == before
    assert "source_row_id" not in {
        column["name"] for column in inspect(engine).get_columns("congress_trades")
    }
    with engine.connect() as connection:
        assert list(connection.execute(select(schema_version.c.version)).scalars()) == [
            1,
            2,
            3,
            4,
            5,
            6,
        ]
