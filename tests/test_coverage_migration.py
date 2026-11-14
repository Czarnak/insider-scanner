"""Previously unfinished filing dates reopen without changing stored trades."""

from datetime import UTC, date, datetime

from sqlalchemy import MetaData, Table, insert, select

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.coverage import CoverageRepository, DateInterval
from insider_scanner.persistence.mappings import congress_to_values, us_to_values
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.schema import scan_coverage
from tests.factories import congress_trade, us_trade


def test_upgrade_reopens_unfinished_coverage_and_preserves_trades(tmp_path):
    engine = create_sqlite_engine(tmp_path / "legacy-coverage.sqlite3")
    bootstrap_database(engine, migrations=MIGRATIONS[:4])
    # Seed and compare the historical columns, independently of current models.
    legacy_us_trades = Table("us_trades", MetaData(), autoload_with=engine)
    legacy_congress_trades = Table("congress_trades", MetaData(), autoload_with=engine)
    values = us_to_values(us_trade())
    with engine.begin() as connection:
        connection.execute(
            insert(legacy_us_trades).values(
                {
                    field: value
                    for field, value in values.items()
                    if field in legacy_us_trades.c
                }
            )
        )
        connection.execute(
            insert(legacy_congress_trades).values(
                {
                    field: value
                    for field, value in congress_to_values(congress_trade()).items()
                    if field in legacy_congress_trades.c
                }
            )
        )
    observed = datetime(2026, 2, 5, 9, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(scan_coverage),
            [
                {
                    "domain": "us",
                    "identifier": "AAPL",
                    "source": "secform4",
                    "start_date": date(2026, 2, 1),
                    "end_date": date(2026, 2, 10),
                    "covered_at": observed,
                },
                {
                    "domain": "congress",
                    "identifier": "*",
                    "source": "house",
                    "start_date": date(2026, 2, 5),
                    "end_date": date(2026, 2, 10),
                    "covered_at": observed,
                },
                {
                    "domain": "eu",
                    "identifier": "GB0002875804",
                    "source": "rns",
                    "start_date": date(2026, 1, 1),
                    "end_date": date(2026, 1, 31),
                    "covered_at": observed,
                },
                {
                    "domain": "congress",
                    "identifier": "*",
                    "source": "senate",
                    "start_date": date(2026, 1, 1),
                    "end_date": date(2026, 1, 31),
                    "covered_at": observed,
                },
            ],
        )
        before_us = connection.execute(select(legacy_us_trades)).all()
        before_congress = connection.execute(select(legacy_congress_trades)).all()
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:5])
        repo = CoverageRepository(engine)
        assert repo.get("us", "AAPL", "secform4") == ()
        assert repo.get("congress", "*", "house") == ()
        assert repo.get("congress", "*", "senate") == ()
        with engine.connect() as connection:
            assert connection.execute(select(legacy_us_trades)).all() == before_us
            assert (
                connection.execute(select(legacy_congress_trades)).all()
                == before_congress
            )
            rows = connection.execute(select(scan_coverage)).all()
            assert len(rows) == 1
            assert rows[0].domain == "eu"
            assert rows[0].covered_at == observed

        # Applying bootstrap again cannot discard a later successful rescan.
        completed = DateInterval(date(2026, 2, 1), date(2026, 2, 10))
        repo.add(
            "us", "AAPL", "secform4", completed, now=datetime(2026, 2, 11, tzinfo=UTC)
        )
        bootstrap_database(engine, migrations=MIGRATIONS[:5])
        assert repo.get("us", "AAPL", "secform4") == (completed,)
    finally:
        engine.dispose()


def test_legacy_compaction_cannot_hide_an_unfinished_day(tmp_path):
    engine = create_sqlite_engine(tmp_path / "compacted-coverage.sqlite3")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:4])
        # Jan 1 was fetched during Jan 1, then compaction on Jan 2 replaced
        # covered_at for the combined row. The timestamp cannot prove Jan 1 safe.
        interval = DateInterval(date(2026, 1, 1), date(2026, 1, 2))
        with engine.begin() as connection:
            connection.execute(
                insert(scan_coverage).values(
                    domain="us",
                    identifier="AAPL",
                    source="openinsider",
                    start_date=interval.start,
                    end_date=interval.end,
                    covered_at=datetime(2026, 1, 2, 9, tzinfo=UTC),
                )
            )
        bootstrap_database(engine, migrations=MIGRATIONS[:5])
        assert CoverageRepository(engine).gaps(
            "us", "AAPL", "openinsider", interval
        ) == (interval,)
    finally:
        engine.dispose()
