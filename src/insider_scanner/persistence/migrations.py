"""Ordered Python migrations for the local SQLite schema."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.engine import Connection

from insider_scanner.core.congress_common import parse_amount_range
from insider_scanner.persistence.schema import (
    congress_trades,
    refresh_state,
    scan_coverage,
    us_trades,
    v1_metadata,
)


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
    connection.exec_driver_sql("ALTER TABLE us_trades ADD COLUMN period_of_report DATE")
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


def _repair_normalized_inputs(connection: Connection) -> None:
    """Repair retained amount labels and allow missing filing dates to refetch.

    The original OpenInsider timestamp was discarded, so a filing date cannot
    be invented here. Preserve its rows and invalidate only affected source
    coverage; a subsequent source reparse can fill the date under the same key.
    """
    affected_tickers: set[str] = set()
    rows = connection.execute(
        select(
            us_trades.c.ticker,
            us_trades.c.source,
            us_trades.c.source_provenance_json,
        ).where(us_trades.c.filing_date.is_(None))
    )
    for row in rows:
        try:
            provenance = json.loads(row.source_provenance_json)
        except (TypeError, ValueError):
            provenance = []
        if row.source == "openinsider" or (
            isinstance(provenance, list) and "openinsider" in provenance
        ):
            affected_tickers.add(row.ticker)

    if affected_tickers:
        identifiers = sorted(affected_tickers | {"*"})
        for table in (scan_coverage, refresh_state):
            connection.execute(
                delete(table).where(
                    table.c.domain == "us",
                    table.c.source == "openinsider",
                    table.c.identifier.in_(identifiers),
                )
            )

    amount_rows = connection.execute(
        select(congress_trades.c.id, congress_trades.c.amount_range).where(
            congress_trades.c.amount_low == 0,
            congress_trades.c.amount_high == 0,
        )
    )
    for amount_row in amount_rows:
        low, high = parse_amount_range(amount_row.amount_range)
        if math.isfinite(low) and math.isfinite(high) and 0 < low <= high:
            connection.execute(
                congress_trades.update()
                .where(congress_trades.c.id == amount_row.id)
                .values(amount_low=low, amount_high=high)
            )


def _repair_unfinished_scan_coverage(connection: Connection) -> None:
    """Reopen dates that were still unfinished when legacy coverage was saved.

    Coverage is only a fetch optimization. Retain every trade and its original
    timestamp while allowing another scan to retrieve later-published filings.
    """
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    rows = connection.execute(
        select(scan_coverage).where(scan_coverage.c.domain.in_(("us", "congress")))
    ).all()
    for row in rows:
        completed_end = min(
            row.end_date,
            row.covered_at.date() - timedelta(days=1),
            yesterday,
        )
        if completed_end < row.start_date:
            connection.execute(
                delete(scan_coverage).where(scan_coverage.c.id == row.id)
            )
        elif completed_end < row.end_date:
            connection.execute(
                scan_coverage.update()
                .where(scan_coverage.c.id == row.id)
                .values(end_date=completed_end)
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
    Migration(
        version=4,
        name="repair_normalized_inputs",
        upgrade=_repair_normalized_inputs,
    ),
    Migration(
        version=5,
        name="repair_unfinished_scan_coverage",
        upgrade=_repair_unfinished_scan_coverage,
    ),
)
