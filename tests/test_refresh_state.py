"""Refresh-state persistence and freshness tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.refresh import RefreshStateRepository, is_fresh


def test_refresh_round_trip_normalizes_utc_and_isolates_full_key(tmp_path):
    engine = create_sqlite_engine(tmp_path / "refresh.sqlite3")
    bootstrap_database(engine)
    repo = RefreshStateRepository(engine)
    supplied = datetime(2026, 6, 7, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    try:
        repo.set("us", "AAPL", "edgar", "latest", supplied)
        repo.set("us", "AAPL", "edgar", "bounded", supplied + timedelta(hours=1))

        assert repo.get("us", "AAPL", "edgar", "latest") == datetime(
            2026, 6, 7, 10, 0, tzinfo=UTC
        )
        assert repo.get("us", "AAPL", "edgar", "missing") is None
    finally:
        engine.dispose()


def test_freshness_is_deterministic_at_ttl_boundary():
    refreshed = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
    ttl = timedelta(hours=1)

    assert is_fresh(
        refreshed,
        now=datetime(2026, 6, 7, 10, 59, tzinfo=UTC),
        ttl=ttl,
    )
    assert not is_fresh(
        refreshed,
        now=datetime(2026, 6, 7, 11, 0, tzinfo=UTC),
        ttl=ttl,
    )
    assert not is_fresh(None, now=refreshed, ttl=ttl)


def test_freshness_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_fresh(
            datetime(2026, 6, 7, 10, 0),
            now=datetime(2026, 6, 7, 11, 0, tzinfo=UTC),
            ttl=timedelta(hours=1),
        )


def test_freshness_rejects_negative_ttl():
    now = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="ttl"):
        is_fresh(now, now=now, ttl=timedelta(seconds=-1))


def test_freshness_treats_future_timestamp_as_fresh_clock_skew():
    now = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)

    assert is_fresh(
        now + timedelta(minutes=5),
        now=now,
        ttl=timedelta(hours=1),
    )


def test_refresh_update_failure_rolls_back_and_wraps_cause(tmp_path):
    engine = create_sqlite_engine(tmp_path / "refresh-failure.sqlite3")
    bootstrap_database(engine)
    repo = RefreshStateRepository(engine)
    original = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
    repo.set("us", "AAPL", "edgar", "latest", original)

    def fail_upsert(
        _connection,
        _cursor,
        statement,
        parameters,
        _context,
        _executemany,
    ):
        if statement.lstrip().upper().startswith("INSERT INTO REFRESH_STATE"):
            raise OperationalError(
                statement, parameters, RuntimeError("forced failure")
            )

    event.listen(engine, "before_cursor_execute", fail_upsert)
    try:
        with pytest.raises(PersistenceError) as exc_info:
            repo.set(
                "us",
                "AAPL",
                "edgar",
                "latest",
                original + timedelta(hours=1),
            )
    finally:
        event.remove(engine, "before_cursor_execute", fail_upsert)

    try:
        assert isinstance(exc_info.value.__cause__, SQLAlchemyError)
        assert repo.get("us", "AAPL", "edgar", "latest") == original
    finally:
        engine.dispose()
