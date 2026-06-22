"""Ordered Python migrations for the local SQLite schema."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from insider_scanner.persistence.schema import v1_metadata


@dataclass(frozen=True)
class Migration:
    """One immutable, ordered schema migration."""

    version: int
    name: str
    upgrade: Callable[[Connection], None]


def _create_initial_schema(connection: Connection) -> None:
    v1_metadata.create_all(connection)


def _add_price_history_source(connection: Connection) -> None:
    connection.exec_driver_sql(
        "ALTER TABLE price_history ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT ''"
    )


def _add_sec_native_fields(connection: Connection) -> None:
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN accession_number VARCHAR(64) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN sec_row_id VARCHAR(128) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN form_type VARCHAR(16) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN cik_issuer VARCHAR(16) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN cik_insider VARCHAR(16) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN period_of_report DATE"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN is_amendment BOOLEAN NOT NULL DEFAULT 0"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN is_derivative BOOLEAN NOT NULL DEFAULT 0"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN document_sha256 VARCHAR(64) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN transaction_code VARCHAR(8) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN acquired_disposed VARCHAR(1) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN direct_or_indirect VARCHAR(1) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN sec_detail_json TEXT NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_us_trades_accession ON us_trades (accession_number)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_us_trades_cik_issuer ON us_trades (cik_issuer)"
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="create_initial_schema",
        upgrade=_create_initial_schema,
    ),
    Migration(
        version=2,
        name="add_price_history_source",
        upgrade=_add_price_history_source,
    ),
    Migration(
        version=3,
        name="add_sec_native_fields",
        upgrade=_add_sec_native_fields,
    ),
)
