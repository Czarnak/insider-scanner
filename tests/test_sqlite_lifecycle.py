"""File-backed SQLite pooling, durability and reader/writer behavior."""

from concurrent.futures import ThreadPoolExecutor
from sqlite3 import ProgrammingError

import pytest
from sqlalchemy import event
from sqlalchemy.pool import QueuePool

from insider_scanner.persistence.engine import (
    SQLITE_POOL_SIZE,
    create_sqlite_engine,
    immediate_transaction,
)


def test_pool_reuses_a_connection_across_threads_and_dispose_closes_it(tmp_path):
    engine = create_sqlite_engine(tmp_path / "pooled.sqlite3")
    connections = []
    event.listen(
        engine, "connect", lambda connection, _: connections.append(connection)
    )
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1

        def read_on_worker():
            with engine.connect() as connection:
                return connection.exec_driver_sql("SELECT 2").scalar_one()

        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(read_on_worker).result(timeout=5) == 2

        assert len(connections) == 1
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == SQLITE_POOL_SIZE
    finally:
        engine.dispose()

    with pytest.raises(ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")


def test_each_physical_connection_has_durable_wal_and_local_settings(tmp_path):
    engine = create_sqlite_engine(tmp_path / "settings.sqlite3", busy_timeout_ms=713)
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert (
                    connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
                    == "wal"
                )
                assert (
                    connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
                )
                assert (
                    connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                )
                assert (
                    connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
                    == 713
                )
                assert (
                    connection.exec_driver_sql("PRAGMA cache_size").scalar_one()
                    == -16384
                )
    finally:
        engine.dispose()


def test_wal_reader_snapshot_does_not_block_an_immediate_writer(tmp_path):
    engine = create_sqlite_engine(
        tmp_path / "reader-writer.sqlite3", busy_timeout_ms=500
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE probe (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO probe VALUES (1)")

        def write_on_worker():
            with immediate_transaction(engine) as connection:
                connection.exec_driver_sql("UPDATE probe SET value=2")

        with engine.connect() as reader, ThreadPoolExecutor(max_workers=1) as executor:
            assert reader.exec_driver_sql("SELECT value FROM probe").scalar_one() == 1
            executor.submit(write_on_worker).result(timeout=5)
            # The read transaction keeps its old snapshot, even though the
            # writer committed successfully while that transaction stayed open.
            assert reader.exec_driver_sql("SELECT value FROM probe").scalar_one() == 1
            reader.commit()
            assert reader.exec_driver_sql("SELECT value FROM probe").scalar_one() == 2
    finally:
        engine.dispose()
