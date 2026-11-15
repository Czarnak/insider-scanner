"""Ordered Python migrations for the local SQLite schema."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import delete, literal, select
from sqlalchemy.engine import Connection

from insider_scanner.core.congress_common import parse_amount_range
from insider_scanner.core.merger import merge_trade_pair
from insider_scanner.persistence.mappings import (
    us_canonical_key,
    us_from_row,
    us_provenance,
    us_to_values,
)
from insider_scanner.persistence.schema import (
    congress_trades,
    refresh_state,
    scan_coverage,
    sec_ingested_filings,
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
    """Rebuild legacy completion claims while preserving every stored trade.

    Legacy compaction replaced covered_at even for untouched intervals, so it
    cannot prove which dates were fetched after completion. Reset this fetch
    optimization once instead of preserving potentially missing filing dates.
    """
    connection.execute(
        delete(scan_coverage).where(scan_coverage.c.domain.in_(("us", "congress")))
    )


def _preserve_sec_financial_meaning(connection: Connection) -> None:
    """Add availability flags and repair SEC meaning supported by stored evidence.

    Old rows did not retain the raw numeric XML. Their zero values cannot be
    classified safely; retain the legacy meaning until the filing is reparsed.
    """
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN shares_missing BOOLEAN NOT NULL DEFAULT 0"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN price_missing BOOLEAN NOT NULL DEFAULT 0"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN value_missing BOOLEAN NOT NULL DEFAULT 0"
    )
    connection.exec_driver_sql(
        "ALTER TABLE us_trades ADD COLUMN shares_owned_after_missing BOOLEAN NOT NULL DEFAULT 0"
    )
    connection.execute(
        us_trades.update()
        .where(us_trades.c.source == "sec_edgar", us_trades.c.is_derivative.is_(True))
        .values(trade_type="Other")
    )
    rows = connection.execute(
        select(us_trades.c.id, us_trades.c.cik_issuer, us_trades.c.cik_insider).where(
            us_trades.c.source == "sec_edgar"
        )
    )
    for row in rows:
        repaired: dict[str, str] = {}
        for field in ("cik_issuer", "cik_insider"):
            original = row._mapping[field]
            candidate = original.strip()
            if (
                1 <= len(candidate) <= 10
                and candidate.isascii()
                and candidate.isdigit()
            ):
                normalized = candidate.zfill(10)
                if normalized != original:
                    repaired[field] = normalized
        if repaired:
            connection.execute(
                us_trades.update().where(us_trades.c.id == row.id).values(**repaired)
            )


def _unify_transaction_identity(connection: Connection) -> None:
    """Rekey stored observations and allow lossy legacy scans to be repaired.

    Historical Congress ordinals and transactions already collapsed by the old
    share bucket cannot be reconstructed. Retain every surviving observation,
    then let a source refresh recover the missing lots. SEC raw keys and SEC
    ingestion progress remain intact.
    """
    connection.exec_driver_sql(
        "ALTER TABLE congress_trades ADD COLUMN source_row_id VARCHAR(128) NOT NULL DEFAULT ''"
    )
    # Move old keys into a disjoint namespace before assigning new hashes;
    # otherwise a new identity can collide with a not-yet-rekeyed old row.
    connection.execute(
        us_trades.update().values(
            canonical_key=literal("pr11:") + us_trades.c.canonical_key
        )
    )
    rows = connection.execute(select(us_trades).order_by(us_trades.c.id)).mappings()
    for row in rows:
        trade = us_from_row(row)
        key = us_canonical_key(trade)
        existing_row = (
            connection.execute(
                select(us_trades).where(us_trades.c.canonical_key == key)
            )
            .mappings()
            .one_or_none()
        )
        if existing_row is None:
            connection.execute(
                us_trades.update()
                .where(us_trades.c.id == row["id"])
                .values(canonical_key=key)
            )
            continue
        existing = us_from_row(existing_row)
        merged = merge_trade_pair(existing, trade)
        values = us_to_values(
            merged, set(us_provenance(existing_row) | us_provenance(row))
        )
        values["created_at"] = min(existing_row["created_at"], row["created_at"])
        values["updated_at"] = max(existing_row["updated_at"], row["updated_at"])
        connection.execute(
            us_trades.update()
            .where(us_trades.c.id == existing_row["id"])
            .values(**values)
        )
        connection.execute(delete(us_trades).where(us_trades.c.id == row["id"]))

    for table in (scan_coverage, refresh_state):
        connection.execute(
            delete(table).where(
                (table.c.domain == "congress")
                | ((table.c.domain == "us") & (table.c.source != "sec_edgar"))
            )
        )


def _remove_duplicate_indexes(connection: Connection) -> None:
    """Retain the UNIQUE indexes that already serve the same lookup keys."""
    connection.exec_driver_sql("DROP INDEX IF EXISTS ix_scan_coverage_lookup")
    connection.exec_driver_sql("DROP INDEX IF EXISTS ix_price_history_symbol_date")


def _add_sec_ingestion_ledger(connection: Connection) -> None:
    """Add independent resume records without trusting legacy JSON claims."""
    sec_ingested_filings.create(connection)


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
    Migration(
        version=6,
        name="preserve_sec_financial_meaning",
        upgrade=_preserve_sec_financial_meaning,
    ),
    Migration(
        version=7,
        name="unify_transaction_identity",
        upgrade=_unify_transaction_identity,
    ),
    Migration(
        version=8,
        name="remove_duplicate_indexes",
        upgrade=_remove_duplicate_indexes,
    ),
    Migration(
        version=9,
        name="add_sec_ingestion_ledger",
        upgrade=_add_sec_ingestion_ledger,
    ),
)
