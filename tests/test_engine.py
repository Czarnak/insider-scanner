"""Tests for SQLite engine PRAGMA configuration (performance + durability)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from insider_scanner.persistence.engine import create_sqlite_engine


@pytest.fixture
def engine(tmp_path):
    engine = create_sqlite_engine(tmp_path / "engine.sqlite3")
    yield engine
    engine.dispose()


def test_connections_enable_wal_journal_mode(engine):
    with engine.connect() as connection:
        mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()

    assert mode == "wal"


def test_connections_use_synchronous_normal(engine):
    # synchronous=NORMAL maps to integer 1.
    with engine.connect() as connection:
        synchronous = connection.execute(text("PRAGMA synchronous")).scalar_one()

    assert synchronous == 1


def test_connections_keep_foreign_keys_and_busy_timeout(engine):
    with engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()

    assert foreign_keys == 1
    assert busy_timeout == 5_000


def test_connections_use_memory_temp_store(engine):
    # temp_store=MEMORY maps to integer 2.
    with engine.connect() as connection:
        temp_store = connection.execute(text("PRAGMA temp_store")).scalar_one()

    assert temp_store == 2


def test_wal_pragmas_do_not_break_busy_timeout_override(tmp_path):
    engine = create_sqlite_engine(tmp_path / "override.sqlite3", busy_timeout_ms=1_500)
    try:
        with engine.connect() as connection:
            busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
            mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        assert busy_timeout == 1_500
        assert mode == "wal"
    finally:
        engine.dispose()
