"""Coverage interval math and persistence tests."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.coverage import (
    CoverageRepository,
    DateInterval,
    normalize_intervals,
    uncovered_gaps,
)
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.schema import scan_coverage


def _interval(start: int, end: int) -> DateInterval:
    return DateInterval(date(2026, 1, start), date(2026, 1, end))


def test_date_interval_rejects_reverse_range():
    with pytest.raises(ValueError, match="start"):
        _interval(2, 1)


def test_normalize_merges_overlap_adjacency_and_ignores_order():
    assert normalize_intervals([_interval(5, 8), _interval(1, 3), _interval(4, 4)]) == (
        _interval(1, 8),
    )


def test_uncovered_gaps_handles_edges_and_internal_holes():
    requested = _interval(1, 10)
    covered = [_interval(2, 3), _interval(5, 8), _interval(20, 25)]

    assert uncovered_gaps(requested, covered) == (
        _interval(1, 1),
        _interval(4, 4),
        _interval(9, 10),
    )


def test_coverage_storage_compacts_and_isolates_domain_identifier_source(tmp_path):
    engine = create_sqlite_engine(tmp_path / "coverage.sqlite3")
    bootstrap_database(engine)
    repo = CoverageRepository(engine)
    try:
        repo.add("us", "AAPL", "secform4", _interval(1, 3))
        repo.add("us", "AAPL", "secform4", _interval(4, 5))
        repo.add("us", "AAPL", "edgar", _interval(2, 2))
        repo.add("eu", "AAPL", "secform4", _interval(8, 9))

        assert repo.get("us", "AAPL", "secform4") == (_interval(1, 5),)
        assert repo.get("us", "AAPL", "edgar") == (_interval(2, 2),)
        assert repo.gaps("us", "AAPL", "secform4", _interval(1, 7)) == (
            _interval(6, 7),
        )
    finally:
        engine.dispose()


def test_coverage_update_failure_rolls_back_and_wraps_cause(tmp_path):
    engine = create_sqlite_engine(tmp_path / "coverage-failure.sqlite3")
    bootstrap_database(engine)
    repo = CoverageRepository(engine)
    repo.add("us", "AAPL", "secform4", _interval(1, 3))

    def fail_insert(
        _connection,
        _cursor,
        statement,
        parameters,
        _context,
        _executemany,
    ):
        if statement.lstrip().upper().startswith("INSERT INTO SCAN_COVERAGE"):
            raise OperationalError(
                statement, parameters, RuntimeError("forced failure")
            )

    event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(PersistenceError) as exc_info:
            repo.add("us", "AAPL", "secform4", _interval(4, 5))
    finally:
        event.remove(engine, "before_cursor_execute", fail_insert)

    try:
        assert isinstance(exc_info.value.__cause__, SQLAlchemyError)
        assert repo.get("us", "AAPL", "secform4") == (_interval(1, 3),)
    finally:
        engine.dispose()


def test_coverage_concurrent_adds_preserve_complete_union(tmp_path):
    engine = create_sqlite_engine(
        tmp_path / "coverage-concurrent.sqlite3",
        busy_timeout_ms=2_000,
    )
    bootstrap_database(engine)
    repo = CoverageRepository(engine)
    repo.add("us", "AAPL", "secform4", _interval(5, 5))
    barrier = threading.Barrier(2)

    def synchronize_reads(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        normalized = statement.upper()
        if normalized.lstrip().startswith("SELECT") and "FROM SCAN_COVERAGE" in (
            normalized
        ):
            try:
                barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass

    event.listen(engine, "after_cursor_execute", synchronize_reads)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    CoverageRepository(engine).add,
                    "us",
                    "AAPL",
                    "secform4",
                    interval,
                )
                for interval in (_interval(1, 2), _interval(8, 9))
            ]
            for future in futures:
                future.result(timeout=10)
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_reads)

    try:
        assert repo.get("us", "AAPL", "secform4") == (
            _interval(1, 2),
            _interval(5, 5),
            _interval(8, 9),
        )
    finally:
        engine.dispose()


def test_coverage_keeps_today_and_future_open_and_can_advance_tomorrow(tmp_path):
    engine = create_sqlite_engine(tmp_path / "unfinished.sqlite3")
    bootstrap_database(engine)
    repo = CoverageRepository(engine)
    now = datetime(2026, 1, 10, 12, tzinfo=UTC)
    try:
        repo.add("us", "AAPL", "secform4", _interval(1, 15), now=now)

        assert repo.get("us", "AAPL", "secform4") == (_interval(1, 9),)
        assert repo.gaps("us", "AAPL", "secform4", _interval(1, 15)) == (
            _interval(10, 15),
        )
        repo.add(
            "us", "AAPL", "secform4", _interval(10, 15), now=now + timedelta(days=1)
        )
        assert repo.get("us", "AAPL", "secform4") == (_interval(1, 10),)
    finally:
        engine.dispose()


@pytest.mark.parametrize("interval", [_interval(10, 10), _interval(11, 15)])
def test_coverage_records_nothing_for_unfinished_dates(tmp_path, interval):
    engine = create_sqlite_engine(tmp_path / "only-unfinished.sqlite3")
    bootstrap_database(engine)
    try:
        repo = CoverageRepository(engine)
        repo.add(
            "congress",
            "*",
            "house",
            interval,
            now=datetime(2026, 1, 10, 12, tzinfo=UTC),
        )
        assert repo.get("congress", "*", "house") == ()
    finally:
        engine.dispose()


def test_coverage_compaction_preserves_component_timestamps(tmp_path):
    engine = create_sqlite_engine(tmp_path / "timestamps.sqlite3")
    bootstrap_database(engine)
    repo = CoverageRepository(engine)
    original = datetime(2026, 2, 1, tzinfo=UTC)
    later = original + timedelta(days=1)
    try:
        repo.add("us", "AAPL", "secform4", _interval(1, 3), now=original)
        repo.add("us", "AAPL", "secform4", _interval(4, 5), now=later)
        repo.add("us", "AAPL", "secform4", _interval(8, 9), now=later)
        with engine.connect() as connection:
            rows = connection.execute(
                select(scan_coverage.c.start_date, scan_coverage.c.covered_at).order_by(
                    scan_coverage.c.start_date
                )
            ).all()
        assert rows == [(date(2026, 1, 1), original), (date(2026, 1, 8), later)]
    finally:
        engine.dispose()


def test_coverage_rejects_naive_observation_time(tmp_path):
    engine = create_sqlite_engine(tmp_path / "naive.sqlite3")
    bootstrap_database(engine)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            CoverageRepository(engine).add(
                "us",
                "AAPL",
                "secform4",
                _interval(1, 3),
                now=datetime(2026, 2, 1),
            )
    finally:
        engine.dispose()


def test_coverage_retains_existing_other_domain_interval_contract(tmp_path):
    engine = create_sqlite_engine(tmp_path / "other-domain.sqlite3")
    bootstrap_database(engine)
    try:
        repo = CoverageRepository(engine)
        repo.add(
            "eu",
            "EXAMPLE",
            "amf",
            _interval(1, 15),
            now=datetime(2026, 1, 10, 12, tzinfo=UTC),
        )
        assert repo.get("eu", "EXAMPLE", "amf") == (_interval(1, 15),)
    finally:
        engine.dispose()
