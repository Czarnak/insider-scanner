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


@pytest.mark.parametrize("skew", [timedelta(seconds=1), timedelta(days=30)])
@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(hours=1)])
def test_freshness_rejects_future_timestamp_after_clock_correction(skew, ttl):
    now = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)

    assert not is_fresh(
        now + skew,
        now=now,
        ttl=ttl,
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


def test_refresh_clear_invalidates_all_modes_for_only_selected_source_key(tmp_path):
    engine = create_sqlite_engine(tmp_path / "clear.sqlite3")
    bootstrap_database(engine)
    repo = RefreshStateRepository(engine)
    observed = datetime(2026, 6, 7, 10, tzinfo=UTC)
    retained_keys = [
        ("us", "AAPL", "openinsider", "latest:200"),
        ("us", "*", "secform4", "latest:200"),
        ("congress", "*", "openinsider", "latest:200"),
    ]
    try:
        for key in retained_keys:
            repo.set(*key, observed)
        repo.set("us", "*", "openinsider", "latest:200", observed)
        repo.set("us", "*", "openinsider", "latest:500", observed)

        repo.clear("us", "*", "openinsider")

        assert repo.get("us", "*", "openinsider", "latest:200") is None
        assert repo.get("us", "*", "openinsider", "latest:500") is None
        assert all(repo.get(*key) == observed for key in retained_keys)
    finally:
        engine.dispose()


def test_refresh_clear_wraps_failure_without_changing_stored_timestamp(tmp_path):
    engine = create_sqlite_engine(tmp_path / "clear-failure.sqlite3")
    bootstrap_database(engine)
    repo = RefreshStateRepository(engine)
    observed = datetime(2026, 6, 7, 10, tzinfo=UTC)
    repo.set("us", "*", "openinsider", "latest:200", observed)

    def fail_delete(
        _connection, _cursor, statement, parameters, _context, _executemany
    ):
        if statement.lstrip().upper().startswith("DELETE FROM REFRESH_STATE"):
            raise OperationalError(
                statement, parameters, RuntimeError("forced failure")
            )

    event.listen(engine, "before_cursor_execute", fail_delete)
    try:
        with pytest.raises(
            PersistenceError, match="refresh state removal failed"
        ) as error:
            repo.clear("us", "*", "openinsider")
    finally:
        event.remove(engine, "before_cursor_execute", fail_delete)
    try:
        assert isinstance(error.value.__cause__, SQLAlchemyError)
        assert repo.get("us", "*", "openinsider", "latest:200") == observed
    finally:
        engine.dispose()
