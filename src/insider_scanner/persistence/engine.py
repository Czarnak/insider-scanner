"""SQLite engine creation and connection configuration."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, NullPool
from sqlalchemy.engine import Connection, Engine, URL

from insider_scanner.persistence.errors import PersistenceError

DEFAULT_BUSY_TIMEOUT_MS = 5_000
SQLITE_IMMEDIATE_OPTION = "sqlite_begin_immediate"


@contextmanager
def immediate_transaction(engine: Engine) -> Iterator[Connection]:
    """Serialize a SQLite read-modify-write transaction before its first read."""
    with engine.connect().execution_options(
        **{SQLITE_IMMEDIATE_OPTION: True}
    ) as connection:
        with connection.begin():
            yield connection


def create_sqlite_engine(
    database_file: Path,
    *,
    echo: bool = False,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Engine:
    """Create a SQLite engine with foreign keys and transactional DDL enabled."""
    resolved_file = Path(database_file)
    if busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be non-negative")

    try:
        resolved_file.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            URL.create("sqlite", database=str(resolved_file)),
            echo=echo,
            connect_args={"timeout": busy_timeout_ms / 1_000},
            poolclass=NullPool,
        )
    except Exception as exc:
        raise PersistenceError(
            f"Failed to create database engine for {resolved_file}"
        ) from exc

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(
        dbapi_connection: Any,
        _connection_record: Any,
    ) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def begin_sqlite_transaction(connection: Any) -> None:
        begin_statement = (
            "BEGIN IMMEDIATE"
            if connection.get_execution_options().get(SQLITE_IMMEDIATE_OPTION)
            else "BEGIN"
        )
        connection.exec_driver_sql(begin_statement)

    return engine
