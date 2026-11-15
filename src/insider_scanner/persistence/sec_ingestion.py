"""Atomically persist SEC trades and durable, accession-based resume state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.sec_ingestion import SecFilingOutcome, SecFilingStatus
from insider_scanner.persistence.engine import immediate_transaction
from insider_scanner.persistence.repositories import (
    UpsertResult,
    UsTradeRepository,
    _wrap,
)
from insider_scanner.persistence.schema import (
    sec_archive_progress,
    sec_archive_retries,
    sec_ingested_filings,
)


@dataclass(frozen=True, slots=True)
class SecIngestionWrite:
    """One attempted filing, with mapped rows only for a successful parse."""

    outcome: SecFilingOutcome
    trades: tuple[InsiderTrade, ...] = ()


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

        trades: list[InsiderTrade] = []
        spans: list[tuple[int, int]] = []
        for entry in entries:
            start = len(trades)
            trades.extend(entry.trades)
            spans.append((start, len(trades)))

        outcomes: list[SecFilingOutcome] = []
        try:
            with immediate_transaction(self._engine) as connection:
                upserts = self._trades.upsert_in_connection(connection, trades)
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
        except SQLAlchemyError as error:
            raise _wrap("SEC ingestion commit", error) from error
        return SecIngestionCommit(upserts, tuple(outcomes))
