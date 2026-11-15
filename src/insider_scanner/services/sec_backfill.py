"""Bulk SEC ingestion backed by the same durable ledger as daily ingestion.

Trades and filing status commit together in bounded batches. Ledger entries are
accession based, survive successful runs and archive renames, and are never
cleared by a targeted CIK run. Old JSON checkpoints are left untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from insider_scanner.core.sec_bulk import (
    SecBulkError,
    bulk_archive_fingerprint,
    bulk_metadata_to_index_row,
    iter_ownership_filings,
)
from insider_scanner.core.sec_client import SecBlockedError, SecClient
from insider_scanner.core.sec_ingestion import (
    SecFilingOutcome,
    SecFilingStatus,
    new_counters,
    process_filing_row,
)
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityPolicy,
)
from insider_scanner.services.common import not_cancelled
from insider_scanner.services.context import PersistenceContext
from insider_scanner.services.sec_persistence import SecTradeBatch, make_persist_sink
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_backfill")


class SecBackfillConfirmationError(Exception):
    """Raised when backfill is invoked without explicit confirmation."""


@dataclass(frozen=True, slots=True)
class SecBackfillSummary:
    """Outcome counters for one backfill run.

    These fields do NOT cleanly partition ``filings_discovered``: a filing that
    parsed but then failed to persist is counted in both ``filings_parsed`` and
    ``failures``; ``skipped_resume``/``skipped_metadata`` count filings that were
    never fetched. Treat each counter independently, not as a sum.
    """

    filings_discovered: int
    filings_parsed: int
    transactions_inserted: int
    transactions_updated: int
    transactions_skipped: int
    skipped_resume: int
    skipped_metadata: int
    failures: int
    unsupported: int = 0
    outcomes: tuple[SecFilingOutcome, ...] = ()
    cancelled: bool = False
    discovered_rows: int = 0
    duplicates: int = 0
    skipped_members: int = 0


class SecBackfillService:
    def __init__(
        self,
        persistence: PersistenceContext,
        *,
        client: SecClient,
        cache_root: Path,
        policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
        cleanup: bool = True,
        batch_filings: int = 200,
        batch_rows: int = 5_000,
    ) -> None:
        self._persistence = persistence
        self._client = client
        self._cache_root = cache_root
        self._policy = policy
        # Retained for callers using the old cache cleanup option. Bulk runs
        # now consume bytes directly and never read, write or purge the cache.
        if batch_filings < 1 or batch_rows < 1:
            raise ValueError("SEC batch limits must be positive")
        self._batch_filings = batch_filings
        self._batch_rows = batch_rows

    def run(
        self,
        zip_path: Path,
        *,
        confirm: bool = False,
        ciks: frozenset[str] | None = None,
        cancelled: Callable[[], bool] = not_cancelled,
        on_progress: Callable[[SecBackfillSummary], None] | None = None,
    ) -> SecBackfillSummary:
        if confirm is not True:
            raise SecBackfillConfirmationError(
                "Full bulk backfill requires explicit confirmation"
            )

        counters = new_counters()
        ledger = self._persistence.sec_ingestion
        archive_id = bulk_archive_fingerprint(zip_path, policy=self._policy)
        scope = (
            "full"
            if ciks is None
            else sha256(",".join(sorted(ciks)).encode("ascii")).hexdigest()
        )
        position = ledger.archive_progress(archive_id, scope)
        batch = SecTradeBatch(
            ledger.commit,
            max_filings=self._batch_filings,
            max_rows=self._batch_rows,
        )
        sink = make_persist_sink(batch)
        skipped_resume = 0
        skipped_metadata = 0
        discovered = 0
        discovered_rows = 0
        duplicates = 0
        skipped_members = 0
        outcomes: list[SecFilingOutcome] = []
        failed_accessions: set[str | None] = set()
        attempted: set[str] = set()
        interrupted = False
        completed_members: list[int] = []
        retry_members: set[int] = set()
        accession_members: dict[str | None, int] = {}

        def snapshot() -> SecBackfillSummary:
            return SecBackfillSummary(
                filings_discovered=discovered,
                filings_parsed=counters["parsed"],
                transactions_inserted=batch.upserts.inserted,
                transactions_updated=batch.upserts.updated,
                transactions_skipped=batch.upserts.skipped,
                skipped_resume=skipped_resume,
                skipped_metadata=skipped_metadata,
                failures=counters["failed"],
                unsupported=counters["unsupported"],
                outcomes=tuple(outcomes),
                cancelled=interrupted,
                discovered_rows=discovered_rows,
                duplicates=duplicates,
                skipped_members=skipped_members,
            )

        def record_committed(*, flush: bool = False, emit: bool = True) -> None:
            committed = batch.take_outcomes(flush=flush)
            for item in committed:
                if not item.complete and item.accession in accession_members:
                    retry_members.add(accession_members[item.accession])
                if item.stage == "persist" and not item.complete:
                    outcomes.append(item)
                    if item.accession not in failed_accessions:
                        counters["failed"] += 1
                        failed_accessions.add(item.accession)
            if committed and emit and on_progress is not None:
                on_progress(snapshot())

        def checkpoint_members() -> None:
            if not completed_members:
                return
            record_committed(flush=True, emit=False)
            ledger.commit_archive_progress(
                archive_id,
                scope,
                [(index, index in retry_members) for index in completed_members],
            )
            completed_members.clear()
            retry_members.clear()
            accession_members.clear()

        def member_error(index: int, reason: str) -> None:
            nonlocal skipped_members
            skipped_members += 1
            retry_members.add(index)
            _log.warning("SEC bulk member skipped index=%d reason=%s", index, reason)

        def member_complete(index: int) -> None:
            completed_members.append(index)
            # Bound replay and write cost even for members with no filings.
            if len(completed_members) >= self._batch_filings:
                checkpoint_members()

        gen = iter_ownership_filings(
            zip_path,
            policy=self._policy,
            ciks=ciks,
            start_member=position.next_member,
            retry_members=position.retry_members,
            on_member_error=member_error,
            on_member_complete=member_complete,
        )
        try:
            for meta in gen:
                if cancelled():
                    interrupted = True
                    break
                discovered_rows += 1
                accession = meta.accession_number
                if accession in attempted:
                    duplicates += 1
                    continue
                attempted.add(accession)
                discovered += 1
                if ledger.is_terminal(accession):
                    skipped_resume += 1
                    continue
                try:
                    row = bulk_metadata_to_index_row(meta)
                except SecBulkError:
                    skipped_metadata += 1
                    continue
                if meta.member_index is not None:
                    accession_members[accession] = meta.member_index
                try:
                    outcome = process_filing_row(
                        row,
                        client=self._client,
                        cache_root=self._cache_root,
                        counters=counters,
                        policy=self._policy,
                        cache=False,
                        on_filing_parsed=sink,
                    )
                except SecBlockedError as error:
                    batch.add_outcome(
                        SecFilingOutcome(
                            accession,
                            SecFilingStatus.RETRYABLE_FAILURE,
                            "fetch",
                            type(error).__name__,
                        )
                    )
                    raise
                if outcome.status is not SecFilingStatus.SUCCESS:
                    outcomes.append(outcome)
                    batch.add_outcome(outcome)
                    if outcome.status is not SecFilingStatus.UNSUPPORTED:
                        failed_accessions.add(outcome.accession)
                    if not outcome.complete and meta.member_index is not None:
                        retry_members.add(meta.member_index)
                record_committed()
        except BaseException:
            # Preserve the abort even if a final flush or callback also fails.
            try:
                record_committed(flush=True, emit=False)
            except BaseException as error:  # noqa: BLE001 - preserve the original abort
                _log.warning(
                    "SEC pending filing flush failed while aborting exception=%s",
                    type(error).__name__,
                )
            raise
        finally:
            gen.close()
        record_committed(flush=True, emit=False)
        checkpoint_members()

        summary = snapshot()
        if on_progress is not None:
            on_progress(summary)
        return summary
