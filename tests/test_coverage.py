"""Coverage interval math and persistence tests."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.coverage import (
    CoverageRepository,
    DateInterval,
    normalize_intervals,
    uncovered_gaps,
)
from insider_scanner.persistence.errors import PersistenceError


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
