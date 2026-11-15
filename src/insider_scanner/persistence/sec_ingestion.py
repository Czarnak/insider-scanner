"""Atomically persist SEC trades and durable, accession-based resume state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError

from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.sec_ingestion import SecFilingOutcome, SecFilingStatus
from insider_scanner.persistence.effective import rebuild_replacements
from insider_scanner.persistence.engine import immediate_transaction
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.repositories import (
    UpsertResult,
    UsTradeRepository,
    _wrap,
)
from insider_scanner.persistence.schema import (
    sec_archive_progress,
    sec_archive_retries,
    sec_ingested_filings,
    trade_replacements,
    us_trades,
)


@dataclass(frozen=True, slots=True)
class SecIngestionWrite:
    """One attempted filing, with mapped rows only for a successful parse."""

    outcome: SecFilingOutcome
    trades: tuple[InsiderTrade, ...] = ()
    representation: Literal["xml", "dataset"] = "xml"


@dataclass(frozen=True, slots=True)
class SecIngestionCommit:
    upserts: UpsertResult
    outcomes: tuple[SecFilingOutcome, ...]


@dataclass(frozen=True, slots=True)
class SecIngestionRecord:
    accession: str
    status: SecFilingStatus
    attempts: int
    stage: str
    reason: str
    updated_at: datetime

    @property
    def complete(self) -> bool:
        return self.status is not SecFilingStatus.RETRYABLE_FAILURE


@dataclass(frozen=True, slots=True)
class SecArchiveProgress:
    next_member: int = 0
    retry_members: frozenset[int] = frozenset()


def _safe_reason(value: str) -> str:
    return "".join(char if char.isprintable() else "?" for char in value)[:2_000]


def _dataset_row(accession: str, row_id: str) -> bool:
    return row_id.startswith(f"{accession}:dataset:")


class SecIngestionRepository:
    """Share filing identity across archives and daily or targeted runs.

    Progress is never inferred from the presence of some trades: an empty filing
    can be successful, and a filing with rejected rows must remain retryable.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._trades = UsTradeRepository(engine)

    def get(self, accession: str) -> SecIngestionRecord | None:
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        select(sec_ingested_filings).where(
                            sec_ingested_filings.c.accession == accession
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    return None
                return SecIngestionRecord(
                    accession=row["accession"],
                    status=SecFilingStatus(row["status"]),
                    attempts=row["attempts"],
                    stage=row["stage"],
                    reason=row["reason"],
                    updated_at=row["updated_at"],
                )
        except SQLAlchemyError as error:
            raise _wrap("SEC ingestion lookup", error) from error

    def is_terminal(self, accession: str) -> bool:
        record = self.get(accession)
        return record is not None and record.complete

    def _prepare_representations(
        self, connection: Connection, entries: Sequence[SecIngestionWrite]
    ) -> tuple[list[SecIngestionWrite], list[RowMapping], int, bool]:
        """Recheck XML precedence while holding the database write lock.

        Dataset surrogate IDs have no documented relationship to XML ordinals.
        A full successful representation replaces the old dataset rows, rather
        than allowing two namespaces to contribute to totals. Existing XML
        evidence, including pre-ledger imports and empty filings, takes priority.
        """
        accessions = list({entry.outcome.accession for entry in entries})
        rows: dict[str, list[RowMapping]] = {}
        records: dict[str, RowMapping] = {}
        for offset in range(0, len(accessions), 300):
            scope = accessions[offset : offset + 300]
            for row in connection.execute(
                select(
                    us_trades.c.accession_number,
                    us_trades.c.sec_row_id,
                    us_trades.c.canonical_key,
                    us_trades.c.ticker,
                    us_trades.c.cik_issuer,
                ).where(
                    us_trades.c.accession_number.in_(scope),
                    us_trades.c.source == "sec_edgar",
                )
            ).mappings():
                rows.setdefault(row["accession_number"], []).append(row)
            for record in connection.execute(
                select(sec_ingested_filings).where(
                    sec_ingested_filings.c.accession.in_(scope)
                )
            ).mappings():
                records[record["accession"]] = record
        incoming_xml = {
            entry.outcome.accession
            for entry in entries
            if entry.representation == "xml"
            and entry.outcome.status is SecFilingStatus.SUCCESS
        }
        prepared: list[SecIngestionWrite] = []
        removed: dict[str, RowMapping] = {}
        skipped = 0
        guarded = False
        for entry in entries:
            accession = entry.outcome.accession or ""
            old = rows.get(accession, [])
            dataset_rows = [
                row for row in old if _dataset_row(accession, row["sec_row_id"])
            ]
            if (
                dataset_rows
                and entry.representation == "xml"
                and any(trade.accession_number != accession for trade in entry.trades)
            ):
                raise ValueError("SEC row accession must match its filing outcome")
            guarded |= bool(dataset_rows) or entry.representation == "dataset"
            if entry.outcome.status is SecFilingStatus.SUCCESS:
                existing_record = records.get(accession)
                xml_present = (
                    accession in incoming_xml
                    or (len(old) > len(dataset_rows) and existing_record is None)
                    or (
                        existing_record is not None
                        and existing_record["status"] == SecFilingStatus.SUCCESS.value
                        and existing_record["stage"] != "dataset"
                    )
                )
                if entry.representation == "dataset" and xml_present:
                    skipped += len(entry.trades)
                    entry = SecIngestionWrite(
                        SecFilingOutcome(accession, SecFilingStatus.SUCCESS)
                    )
                else:
                    incoming = {trade.sec_row_id for trade in entry.trades}
                    removed.update(
                        (row["canonical_key"], row)
                        for row in (
                            old if entry.representation == "dataset" else dataset_rows
                        )
                        if row["sec_row_id"] not in incoming
                    )
            prepared.append(entry)
        return prepared, list(removed.values()), skipped, guarded

    def archive_progress(self, archive_id: str, scope: str) -> SecArchiveProgress:
        try:
            with self._engine.connect() as connection:
                position = connection.scalar(
                    select(sec_archive_progress.c.next_member).where(
                        sec_archive_progress.c.archive_id == archive_id,
                        sec_archive_progress.c.scope == scope,
                    )
                )
                retries = connection.scalars(
                    select(sec_archive_retries.c.member_index).where(
                        sec_archive_retries.c.archive_id == archive_id,
                        sec_archive_retries.c.scope == scope,
                    )
                )
                return SecArchiveProgress(position or 0, frozenset(retries))
        except SQLAlchemyError as error:
            raise _wrap("SEC archive progress lookup", error) from error

    def commit_archive_progress(
        self, archive_id: str, scope: str, members: Sequence[tuple[int, bool]]
    ) -> None:
        """Advance only after filing batches commit; retain failed members.

        A crash between the filing commit and this transaction only replays
        discovery. The accession ledger prevents duplicate successful fetches.
        Retry rows are updated individually instead of rewriting a growing set.
        """
        if not members:
            return
        if any(index < 0 for index, _retry in members):
            raise ValueError("Archive member positions must not be negative")
        try:
            with immediate_transaction(self._engine) as connection:
                statement = insert(sec_archive_progress).values(
                    archive_id=archive_id,
                    scope=scope,
                    next_member=max(index for index, _ in members) + 1,
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=["archive_id", "scope"],
                        set_={
                            "next_member": func.max(
                                sec_archive_progress.c.next_member,
                                statement.excluded.next_member,
                            )
                        },
                    )
                )
                completed = [index for index, retry in members if not retry]
                if completed:
                    connection.execute(
                        delete(sec_archive_retries).where(
                            sec_archive_retries.c.archive_id == archive_id,
                            sec_archive_retries.c.scope == scope,
                            sec_archive_retries.c.member_index.in_(completed),
                        )
                    )
                failures = [
                    {"archive_id": archive_id, "scope": scope, "member_index": index}
                    for index, retry in members
                    if retry
                ]
                if failures:
                    connection.execute(
                        insert(sec_archive_retries).on_conflict_do_nothing(), failures
                    )
        except SQLAlchemyError as error:
            raise _wrap("SEC archive progress commit", error) from error

    def commit(self, entries: Sequence[SecIngestionWrite]) -> SecIngestionCommit:
        """Publish status and rows together, or roll back the complete batch.

        Invalid rows are isolated using the trade repository's savepoints. Good
        rows still commit, with their containing filing marked retryable.
        """
        if not entries:
            return SecIngestionCommit(UpsertResult(), ())
        for entry in entries:
            if not entry.outcome.accession:
                raise ValueError("SEC ingestion requires a filing accession")
            if entry.trades and entry.outcome.status is not SecFilingStatus.SUCCESS:
                raise ValueError("Only successful SEC parses may contain trades")
            if entry.representation not in {"xml", "dataset"}:
                raise ValueError("Unknown SEC representation")
            if entry.representation == "dataset" and any(
                trade.accession_number != entry.outcome.accession
                or trade.source != "sec_edgar"
                or not _dataset_row(entry.outcome.accession, trade.sec_row_id)
                for trade in entry.trades
            ):
                raise ValueError("Invalid SEC dataset row identity")

        outcomes: list[SecFilingOutcome] = []
        try:
            with immediate_transaction(self._engine) as connection:
                entries, removed, skipped, guarded = self._prepare_representations(
                    connection, entries
                )
                trades: list[InsiderTrade] = []
                spans: list[tuple[int, int]] = []
                for entry in entries:
                    start = len(trades)
                    trades.extend(entry.trades)
                    spans.append((start, len(trades)))
                upserts = self._trades.upsert_in_connection(connection, trades)
                if guarded and upserts.errors:
                    # Replacing representations must not leave the previous
                    # dataset plus a partial XML repair visible together. The
                    # bounded batch remains wholly available for retry.
                    raise PersistenceError(
                        "SEC representation replacement rejected rows"
                    )
                upserts += UpsertResult(skipped=skipped)
                for offset in range(0, len(removed), 300):
                    keys = [
                        row["canonical_key"] for row in removed[offset : offset + 300]
                    ]
                    connection.execute(
                        delete(trade_replacements).where(
                            trade_replacements.c.market == "us",
                            or_(
                                trade_replacements.c.canonical_key.in_(keys),
                                trade_replacements.c.replacement_key.in_(keys),
                            ),
                        )
                    )
                    connection.execute(
                        delete(us_trades).where(us_trades.c.canonical_key.in_(keys))
                    )
                now = datetime.now(UTC)
                values = []
                for entry, (start, end) in zip(entries, spans, strict=True):
                    outcome = entry.outcome
                    errors = [
                        item.message
                        for item in upserts.errors
                        if start <= item.index < end
                    ]
                    if errors:
                        outcome = replace(
                            outcome,
                            status=SecFilingStatus.RETRYABLE_FAILURE,
                            stage="persist",
                            reason="; ".join(errors),
                        )
                    outcome = replace(outcome, reason=_safe_reason(outcome.reason))
                    outcomes.append(outcome)
                    values.append(
                        {
                            "accession": outcome.accession,
                            "status": outcome.status.value,
                            "attempts": 1,
                            "stage": outcome.stage,
                            "reason": outcome.reason,
                            "updated_at": now,
                        }
                    )
                statement = insert(sec_ingested_filings)
                # Downloads happen outside this transaction. An overlapping run
                # can finish successfully while another still holds a stale
                # fetch/mapping failure; that attempt must not erase success.
                keep_success = and_(
                    sec_ingested_filings.c.status == SecFilingStatus.SUCCESS.value,
                    statement.excluded.status != SecFilingStatus.SUCCESS.value,
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[sec_ingested_filings.c.accession],
                        set_={
                            "status": case(
                                (keep_success, sec_ingested_filings.c.status),
                                else_=statement.excluded.status,
                            ),
                            "attempts": sec_ingested_filings.c.attempts + 1,
                            "stage": case(
                                (keep_success, sec_ingested_filings.c.stage),
                                else_=statement.excluded.stage,
                            ),
                            "reason": case(
                                (keep_success, sec_ingested_filings.c.reason),
                                else_=statement.excluded.reason,
                            ),
                            "updated_at": statement.excluded.updated_at,
                        },
                    ),
                    values,
                )
                if trades or removed:
                    # Status is now final for this transaction. A partly
                    # rejected amendment must not replace a complete original.
                    rebuild_replacements(
                        connection,
                        us_trades,
                        tickers={
                            trade.ticker.strip().upper()
                            for trade in trades
                            if isinstance(trade.ticker, str)
                        }
                        | {row["ticker"] for row in removed},
                        issuers={
                            trade.cik_issuer
                            for trade in trades
                            if isinstance(trade.cik_issuer, str) and trade.cik_issuer
                        }
                        | {row["cik_issuer"] for row in removed if row["cik_issuer"]},
                    )
        except SQLAlchemyError as error:
            raise _wrap("SEC ingestion commit", error) from error
        return SecIngestionCommit(upserts, tuple(outcomes))
