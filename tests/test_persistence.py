"""Tests for the SQLAlchemy Core persistence foundation."""

from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer, Table, func, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from insider_scanner.persistence.bootstrap import (
    _validate_migrations,
    bootstrap_database,
)
from insider_scanner.persistence.engine import create_sqlite_engine
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.migrations import MIGRATIONS, Migration
from insider_scanner.persistence.schema import (
    congress_trades,
    european_trades,
    metadata,
    price_history,
    refresh_state,
    scan_coverage,
    schema_version,
    us_trades,
)
from insider_scanner.persistence.types import UTCDateTime

EXPECTED_TABLES = {
    "trade_replacements",
    "schema_version",
    "us_trades",
    "congress_trades",
    "european_trades",
    "scan_coverage",
    "refresh_state",
    "price_history",
    "sec_ingested_filings",
    "sec_archive_progress",
    "sec_archive_retries",
}
EXPECTED_TABLES.update(
    f"{market}_trades_search{suffix}"
    for market in ("us", "congress", "european")
    for suffix in ("", "_config", "_content", "_data", "_docsize", "_idx")
)

EXPECTED_UNIQUE_CONSTRAINTS = {
    "us_trades": {"uq_us_trades_canonical_key"},
    "congress_trades": {"uq_congress_trades_canonical_key"},
    "european_trades": {"uq_european_trades_canonical_key"},
    "scan_coverage": {"uq_scan_coverage_interval"},
    "price_history": {"uq_price_history_symbol_date"},
}

EXPECTED_INDEXES = {
    "us_trades": {
        "ix_us_trades_ticker_filing_date",
        "ix_us_trades_accession",
        "ix_us_trades_cik_issuer",
        "ix_us_trades_feed_date",
        "ix_us_trades_feed_issuer",
        "ix_us_trades_feed_person",
        "ix_us_trades_latest",
    },
    "congress_trades": {
        "ix_congress_trades_official_filing_date",
        "ix_congress_trades_source_filing_date",
        "ix_congress_trades_feed_date",
        "ix_congress_trades_feed_issuer",
        "ix_congress_trades_feed_person",
    },
    "european_trades": {
        "ix_european_trades_isin_trade_date",
        "ix_european_trades_source_trade_date",
        "ix_european_trades_feed_date",
        "ix_european_trades_feed_issuer",
        "ix_european_trades_feed_person",
        "ix_european_trades_latest",
    },
    "scan_coverage": set(),
    "refresh_state": {
        "ix_refresh_state_refreshed_at",
        "ix_refresh_state_domain_refreshed_at",
    },
    "price_history": set(),
}


@pytest.fixture
def engine(tmp_path):
    """Keep migrations uninitialized; unlike the shared repository fixture."""
    database_file = tmp_path / "db" / "insider-scanner.sqlite3"
    engine = create_sqlite_engine(database_file)
    yield engine
    engine.dispose()


def test_sqlite_connections_enforce_foreign_keys(engine):
    with engine.connect() as connection:
        enabled = connection.execute(text("PRAGMA foreign_keys")).scalar_one()

    assert enabled == 1


def test_concurrent_bootstrap_serializes_atomic_migrations(tmp_path):
    database_file = tmp_path / "concurrent.sqlite3"
    migration_started = threading.Event()
    release_migration = threading.Event()
    migration_calls = 0
    migration_calls_lock = threading.Lock()

    def slow_initial_migration(connection):
        nonlocal migration_calls
        with migration_calls_lock:
            migration_calls += 1
        connection.execute(text("CREATE TABLE concurrent_probe (id INTEGER)"))
        migration_started.set()
        assert release_migration.wait(timeout=5)

    migrations = (
        Migration(version=1, name="slow-initial", upgrade=slow_initial_migration),
    )
    engines = [
        create_sqlite_engine(database_file, busy_timeout_ms=5_000) for _ in range(4)
    ]

    def run_bootstrap(engine):
        bootstrap_database(engine, migrations=migrations)

    with ThreadPoolExecutor(max_workers=len(engines)) as executor:
        first = executor.submit(run_bootstrap, engines[0])
        assert migration_started.wait(timeout=5)
        remaining = [executor.submit(run_bootstrap, engine) for engine in engines[1:]]
        time.sleep(0.1)
        release_migration.set()
        for future in [first, *remaining]:
            future.result(timeout=10)

    for engine in engines:
        engine.dispose()
    assert migration_calls == 1


def test_bootstrap_creates_all_tables_constraints_and_indexes(engine):
    bootstrap_database(engine)
    inspector = inspect(engine)

    assert set(inspector.get_table_names()) == EXPECTED_TABLES
    for table_name, expected_names in EXPECTED_UNIQUE_CONSTRAINTS.items():
        with engine.connect() as connection:
            definition = connection.execute(
                text(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"
                ),
                {"name": table_name},
            ).scalar_one()
        for constraint in expected_names:
            assert f"CONSTRAINT {constraint} UNIQUE" in definition
    for table_name, expected_names in EXPECTED_INDEXES.items():
        # SQLAlchemy cannot reflect SQLite expression indexes; inspect their
        # persisted names directly so Unicode person indexes are checked too.
        with engine.connect() as connection:
            actual_names = set(
                connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type = 'index' "
                        "AND tbl_name = :name AND name NOT LIKE 'sqlite_autoindex%'"
                    ),
                    {"name": table_name},
                ).scalars()
            )
        assert expected_names == actual_names

    coverage_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("scan_coverage")
    }
    assert "ck_scan_coverage_date_order" in coverage_checks


def test_migration_one_ignores_tables_added_to_runtime_metadata(tmp_path):
    runtime_only = Table(
        "runtime_only_after_v1",
        metadata,
        Column("id", Integer, primary_key=True),
    )
    isolated_engine = create_sqlite_engine(tmp_path / "frozen-v1.sqlite3")
    try:
        bootstrap_database(isolated_engine)
        assert "runtime_only_after_v1" not in inspect(isolated_engine).get_table_names()
    finally:
        isolated_engine.dispose()
        metadata.remove(runtime_only)


def test_duplicate_index_upgrade_preserves_rows_and_unique_constraints(engine):
    bootstrap_database(engine, migrations=MIGRATIONS[:7])
    assert {
        index["name"] for index in inspect(engine).get_indexes("scan_coverage")
    } == {"ix_scan_coverage_lookup"}
    assert {
        index["name"] for index in inspect(engine).get_indexes("price_history")
    } == {"ix_price_history_symbol_date"}

    coverage_values = {
        "domain": "us",
        "source": "sec_edgar",
        "identifier": "AAPL",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 1, 2),
    }
    price_values = {
        "symbol": "AAPL",
        "price_date": date(2026, 1, 1),
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 104.0,
        "source": "test",
    }
    with engine.begin() as connection:
        connection.execute(insert(scan_coverage).values(**coverage_values))
        connection.execute(insert(price_history).values(**price_values))
        rows_before = {
            table.name: connection.execute(select(table)).mappings().all()
            for table in (scan_coverage, price_history)
        }

    bootstrap_database(engine)

    with engine.connect() as connection:
        rows_after = {
            table.name: connection.execute(select(table)).mappings().all()
            for table in (scan_coverage, price_history)
        }
    assert rows_after == rows_before

    inspector = inspect(engine)
    for table, values in (
        (scan_coverage, coverage_values),
        (price_history, price_values),
    ):
        assert inspector.get_indexes(table.name) == []
        assert table.indexes == set()
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert(table).values(**values))


def test_schema_uses_date_columns_for_domain_dates():
    assert metadata.tables["us_trades"].c.trade_date.type.python_type is date
    assert metadata.tables["us_trades"].c.filing_date.type.python_type is date
    assert metadata.tables["congress_trades"].c.trade_date.type.python_type is date
    assert metadata.tables["european_trades"].c.trade_date.type.python_type is date
    assert metadata.tables["scan_coverage"].c.start_date.type.python_type is date
    assert metadata.tables["price_history"].c.price_date.type.python_type is date


def test_schema_accepts_dataclass_valid_empty_congress_fields(engine):
    bootstrap_database(engine)

    with engine.begin() as connection:
        connection.execute(
            insert(congress_trades).values(
                canonical_key="congress-empty-fields",
                official_name="",
                chamber="",
                filing_date=None,
                trade_type="Other",
                source="",
            )
        )


def test_schema_accepts_dataclass_valid_empty_european_country_and_missing_date(
    engine,
):
    bootstrap_database(engine)

    with engine.begin() as connection:
        connection.execute(
            insert(european_trades).values(
                canonical_key="eu-empty-country-missing-date",
                isin="",
                country="",
                trade_date=None,
                trade_type="Other",
                source="",
            )
        )


def test_missing_trade_date_uniqueness_uses_non_null_canonical_key(engine):
    bootstrap_database(engine)
    shared_values = {
        "isin": "",
        "country": "",
        "trade_date": None,
        "trade_type": "Other",
        "source": "",
    }

    with engine.begin() as connection:
        connection.execute(
            insert(european_trades),
            [
                {"canonical_key": "missing-date-1", **shared_values},
                {"canonical_key": "missing-date-2", **shared_values},
            ],
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(european_trades).values(
                canonical_key="missing-date-1",
                **shared_values,
            )
        )

    with engine.connect() as connection:
        row_count = connection.execute(
            select(func.count()).select_from(european_trades)
        ).scalar_one()
    assert row_count == 2


def test_bootstrap_sets_schema_version_to_one(engine):
    bootstrap_database(engine)

    with engine.connect() as connection:
        versions = connection.execute(
            select(schema_version.c.version).order_by(schema_version.c.version)
        ).scalars()

        assert list(versions) == [migration.version for migration in MIGRATIONS]


def test_bootstrap_is_idempotent(engine):
    bootstrap_database(engine)
    bootstrap_database(engine)

    with engine.connect() as connection:
        versions = connection.execute(select(schema_version.c.version)).scalars()

        assert list(versions) == [migration.version for migration in MIGRATIONS]
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


@pytest.mark.parametrize(
    ("history", "message", "migrations"),
    [
        ([1, 999], "unsupported future version 999", None),
        ([1, 1], "duplicate version 1", None),
        (
            [1, 3],
            "missing version 2",
            tuple(
                Migration(
                    version=version,
                    name=f"supported-{version}",
                    upgrade=lambda _: None,
                )
                for version in (1, 2, 3)
            ),
        ),
        ([0], "invalid version 0", None),
        (["bad"], "invalid version 'bad'", None),
    ],
)
def test_bootstrap_rejects_invalid_stored_schema_history(
    tmp_path,
    history,
    message,
    migrations,
):
    database_file = tmp_path / "invalid-history.sqlite3"
    with closing(sqlite3.connect(database_file)) as connection, connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        connection.executemany(
            "INSERT INTO schema_version (version) VALUES (?)",
            [(version,) for version in history],
        )

    engine = create_sqlite_engine(database_file)
    try:
        with pytest.raises(PersistenceError, match=message) as exc_info:
            if migrations is None:
                bootstrap_database(engine)
            else:
                bootstrap_database(engine, migrations=migrations)
    finally:
        engine.dispose()

    assert str(history) in str(exc_info.value)


@pytest.mark.parametrize(
    ("versions", "message"),
    [
        ([2, 1], "strictly increasing"),
        ([1, 1], "duplicate version 1"),
        ([1, 3], "missing version 2"),
    ],
)
def test_migration_sequence_validation_rejects_invalid_versions(versions, message):
    migrations = tuple(
        Migration(version=version, name=f"migration-{index}", upgrade=lambda _: None)
        for index, version in enumerate(versions)
    )

    with pytest.raises(PersistenceError, match=message):
        _validate_migrations(migrations)


def test_failed_migration_rolls_back_and_wraps_error(engine):
    def fail_after_ddl(connection):
        connection.execute(
            text("CREATE TABLE should_roll_back (id INTEGER PRIMARY KEY)")
        )
        raise RuntimeError("migration exploded")

    migrations = (Migration(version=1, name="fail", upgrade=fail_after_ddl),)

    with pytest.raises(
        PersistenceError, match="migration 1 \\(fail\\) failed"
    ) as exc_info:
        bootstrap_database(engine, migrations=migrations)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert inspect(engine).get_table_names() == []


def test_utc_datetime_round_trip_normalizes_offset_to_aware_utc(engine):
    bootstrap_database(engine)
    supplied = datetime(
        2026,
        6,
        7,
        18,
        30,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )

    with engine.begin() as connection:
        connection.execute(
            insert(refresh_state).values(
                domain="test",
                source="test",
                identifier="utc-round-trip",
                mode="latest",
                refreshed_at=supplied,
            )
        )
    with engine.connect() as connection:
        stored = connection.execute(
            select(refresh_state.c.refreshed_at).where(
                refresh_state.c.identifier == "utc-round-trip"
            )
        ).scalar_one()

    assert stored == supplied.astimezone(UTC)
    assert stored.tzinfo is UTC


def test_utc_datetime_rejects_naive_values(engine):
    bootstrap_database(engine)

    with pytest.raises(StatementError) as exc_info, engine.begin() as connection:
        connection.execute(
            insert(refresh_state).values(
                domain="test",
                source="test",
                identifier="naive",
                mode="latest",
                refreshed_at=datetime(2026, 6, 7, 12, 0),
            )
        )

    assert isinstance(exc_info.value.orig, TypeError)
    assert "timezone-aware" in str(exc_info.value.orig)


def test_all_persisted_timestamp_columns_use_utc_datetime():
    timestamp_columns = {
        "schema_version": ("applied_at",),
        "us_trades": ("created_at", "updated_at"),
        "congress_trades": ("created_at", "updated_at"),
        "european_trades": ("created_at", "updated_at"),
        "scan_coverage": ("covered_at",),
        "refresh_state": ("refreshed_at",),
        "price_history": ("created_at",),
    }

    for table_name, column_names in timestamp_columns.items():
        for column_name in column_names:
            assert isinstance(
                metadata.tables[table_name].c[column_name].type,
                UTCDateTime,
            )


def test_coverage_and_refresh_schema_include_complete_storage_keys():
    assert {
        "domain",
        "identifier",
        "source",
        "start_date",
        "end_date",
    } <= set(scan_coverage.c.keys())
    assert {"domain", "identifier", "source", "mode"} <= set(refresh_state.c.keys())


def test_us_schema_preserves_cross_source_provenance():
    assert "source_provenance_json" in metadata.tables["us_trades"].c


def test_european_schema_preserves_cross_source_provenance():
    assert "source_provenance_json" in metadata.tables["european_trades"].c


SEC_V3_COLUMNS = {
    "accession_number",
    "sec_row_id",
    "form_type",
    "cik_issuer",
    "cik_insider",
    "period_of_report",
    "is_amendment",
    "is_derivative",
    "document_sha256",
    "transaction_code",
    "acquired_disposed",
    "direct_or_indirect",
    "sec_detail_json",
}

SEC_V3_INDEXES = {
    "ix_us_trades_accession",
    "ix_us_trades_cik_issuer",
}


def test_bootstrap_v3_adds_sec_columns_and_indexes(engine):
    bootstrap_database(engine)
    inspector = inspect(engine)

    actual_columns = {col["name"] for col in inspector.get_columns("us_trades")}
    assert SEC_V3_COLUMNS <= actual_columns

    with engine.connect() as connection:
        actual_indexes = set(
            connection.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'us_trades'"
                )
            ).scalars()
        )
    assert SEC_V3_INDEXES <= actual_indexes


def test_v2_to_v3_upgrade_preserves_legacy_row(tmp_path):
    # Bootstrap only to version 2, insert a legacy row, then upgrade fully.
    database_file = tmp_path / "v2-to-v3.sqlite3"
    engine = create_sqlite_engine(database_file)
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:2])

        with engine.begin() as connection:
            connection.execute(
                insert(us_trades).values(
                    canonical_key="legacy-row-key",
                    ticker="AAPL",
                    company="Apple Inc",
                    insider_name="John Doe",
                    insider_title="CEO",
                    trade_type="Buy",
                    trade_date=date(2024, 1, 15),
                    filing_date=date(2024, 1, 20),
                    shares=1000.0,
                    price=150.0,
                    value=150000.0,
                    shares_owned_after=5000.0,
                    source="openinsider",
                    source_provenance_json="[]",
                    edgar_url="",
                    is_congress=False,
                    congress_member="",
                )
            )

        # Apply all pending migrations, including the new raw identity.
        bootstrap_database(engine)

        with engine.connect() as connection:
            # schema_version rows include all applied migrations
            versions = list(
                connection.execute(
                    select(schema_version.c.version).order_by(schema_version.c.version)
                ).scalars()
            )
            assert versions == [migration.version for migration in MIGRATIONS]

            # Legacy row preserved with v3 column defaults
            row = (
                connection.execute(select(us_trades).where(us_trades.c.id == 1))
                .mappings()
                .one()
            )

            assert row["ticker"] == "AAPL"
            from insider_scanner.persistence.mappings import (
                us_canonical_key,
                us_from_row,
            )

            assert row["canonical_key"] == us_canonical_key(us_from_row(row))
            assert row["accession_number"] == ""
            assert row["sec_row_id"] == ""
            assert row["form_type"] == ""
            assert row["cik_issuer"] == ""
            assert row["cik_insider"] == ""
            assert row["period_of_report"] is None
            assert not row["is_amendment"]
            assert not row["is_derivative"]
            assert row["document_sha256"] == ""
            assert row["transaction_code"] == ""
            assert row["acquired_disposed"] == ""
            assert row["direct_or_indirect"] == ""
            assert row["sec_detail_json"] == ""
    finally:
        engine.dispose()


def test_us_trades_sec_fields_round_trip(engine):
    bootstrap_database(engine)

    with engine.begin() as connection:
        connection.execute(
            insert(us_trades).values(
                canonical_key="sec-native-round-trip",
                ticker="MSFT",
                company="Microsoft Corp",
                insider_name="Jane Smith",
                insider_title="CFO",
                trade_type="Sell",
                trade_date=date(2025, 3, 10),
                filing_date=date(2025, 3, 12),
                shares=500.0,
                price=300.0,
                value=150000.0,
                shares_owned_after=2000.0,
                source="sec",
                source_provenance_json="[]",
                edgar_url="https://www.sec.gov/Archives/edgar/data/789019/000078901925000001/",
                is_congress=False,
                congress_member="",
                accession_number="0000789019-25-000001",
                sec_row_id="0000789019-25-000001-row-1",
                form_type="4",
                cik_issuer="0000789019",
                cik_insider="0000654321",
                period_of_report=date(2025, 3, 10),
                is_amendment=False,
                is_derivative=False,
                document_sha256="abc123def456" + "0" * 52,
                transaction_code="S",
                acquired_disposed="D",
                direct_or_indirect="D",
                sec_detail_json='{"footnotes": []}',
            )
        )

    with engine.connect() as connection:
        row = (
            connection.execute(
                select(us_trades).where(
                    us_trades.c.canonical_key == "sec-native-round-trip"
                )
            )
            .mappings()
            .one()
        )

    assert row["accession_number"] == "0000789019-25-000001"
    assert row["sec_row_id"] == "0000789019-25-000001-row-1"
    assert row["form_type"] == "4"
    assert row["cik_issuer"] == "0000789019"
    assert row["cik_insider"] == "0000654321"
    assert row["period_of_report"] == date(2025, 3, 10)
    assert row["is_amendment"] is False or not row["is_amendment"]
    assert row["is_derivative"] is False or not row["is_derivative"]
    assert row["document_sha256"] == "abc123def456" + "0" * 52
    assert row["transaction_code"] == "S"
    assert row["acquired_disposed"] == "D"
    assert row["direct_or_indirect"] == "D"
    assert row["sec_detail_json"] == '{"footnotes": []}'
