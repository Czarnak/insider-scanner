"""SQLite engine creation and connection configuration."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import SQLITE_BUSY
from sqlite3 import Error as SQLiteError
from time import monotonic, sleep
from typing import Any

from sqlalchemy import QueuePool, create_engine, event
from sqlalchemy.engine import URL, Connection, Engine

from insider_scanner.persistence.errors import PersistenceError

DEFAULT_BUSY_TIMEOUT_MS = 5_000
SQLITE_POOL_SIZE = 4
SQLITE_IMMEDIATE_OPTION = "sqlite_begin_immediate"


@contextmanager
def immediate_transaction(engine: Engine) -> Iterator[Connection]:
    """Serialize a SQLite read-modify-write transaction before its first read."""
    with (
        engine.connect().execution_options(
            **{SQLITE_IMMEDIATE_OPTION: True}
        ) as connection,
        connection.begin(),
    ):
        yield connection


def create_sqlite_engine(
    database_file: Path,
    *,
    echo: bool = False,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Engine:
    """Create a bounded, reusable file-database pool with transactional DDL.

    WAL lets GUI readers retain a snapshot while ingestion commits. FULL
    synchronization keeps a committed batch durable before a file checkpoint
    can acknowledge it, including after power loss. Every connection belongs
    to its acquiring caller until the with-block returns it to the pool.
    """
    resolved_file = Path(database_file)
    if busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be non-negative")

    try:
        resolved_file.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            URL.create("sqlite", database=str(resolved_file)),
            echo=echo,
            connect_args={
                "timeout": busy_timeout_ms / 1_000,
                "check_same_thread": False,
            },
            poolclass=QueuePool,
            pool_size=SQLITE_POOL_SIZE,
            max_overflow=0,
        )
    except Exception as exc:
        raise PersistenceError(
            f"Failed to create database engine for {resolved_file}"
        ) from exc

    @event.listens_for(engine, "first_connect")
    def enable_sqlite_wal(
        dbapi_connection: Any,
        _connection_record: Any,
    ) -> None:
        # Journal mode persists in the file. Change it once per pool, before
        # any BEGIN; querying first avoids taking a journal-mode write lock
        # when another process already enabled WAL and is ingesting data.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            # Simultaneous first opens can both observe DELETE. SQLite may
            # reject that journal-mode lock upgrade immediately, bypassing its
            # busy handler. Retry BUSY explicitly within the caller's budget;
            # disabling the native wait prevents each attempt restarting it.
            cursor.execute("PRAGMA busy_timeout=0")
            deadline = monotonic() + busy_timeout_ms / 1_000
            while True:
                try:
                    mode = cursor.execute("PRAGMA journal_mode").fetchone()[0]
                    if mode.casefold() != "wal":
                        mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    if mode.casefold() != "wal":
                        raise PersistenceError(
                            f"SQLite WAL initialization returned journal mode {mode!r}"
                        )
                    break
                except SQLiteError as error:
                    code = getattr(error, "sqlite_errorcode", None)
                    remaining = deadline - monotonic()
                    if code is None or code & 0xFF != SQLITE_BUSY or remaining <= 0:
                        raise PersistenceError(
                            "SQLite WAL initialization failed within "
                            f"{busy_timeout_ms} ms: {type(error).__name__}: {error}"
                        ) from error
                    sleep(min(0.01, remaining))
        finally:
            cursor.close()

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(
        dbapi_connection: Any,
        _connection_record: Any,
    ) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=FULL")
            # Bound page caching to 16 MiB per physical connection (64 MiB
            # across the pool); SQLite's default WAL checkpoint policy stays
            # enabled so long imports do not grow the journal without a limit.
            cursor.execute("PRAGMA cache_size=-16384")
            # This is a local integer connection option, not SQL supplied by a
            # user. SQLite PRAGMA does not accept a bind placeholder here.
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query, python.lang.security.audit.formatted-sql-query.formatted-sql-query
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
