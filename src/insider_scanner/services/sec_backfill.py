"""Bulk SEC ingestion backed by the same durable ledger as daily ingestion.

Trades and filing status commit together in bounded batches. Ledger entries are
accession based, survive successful runs and archive renames, and are never
cleared by a targeted CIK run. Old JSON checkpoints are left untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from insider_scanner.core.sec_bulk import (
    SecBulkError,
    bulk_metadata_to_index_row,
    iter_ownership_filings,
)
from insider_scanner.core.sec_client import SecBlockedError, SecClient
from insider_scanner.core.sec_downloader import purge_stale_cache
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
        self._cleanup = cleanup
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
        batch = SecTradeBatch(
            ledger.commit,
            max_filings=self._batch_filings,
            max_rows=self._batch_rows,
        )
        sink = make_persist_sink(batch)
        skipped_resume = 0
        skipped_metadata = 0
        discovered = 0
        outcomes: list[SecFilingOutcome] = []
        failed_accessions: set[str | None] = set()
        attempted: set[str] = set()
        interrupted = False

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
            )

        def record_committed(*, flush: bool = False, emit: bool = True) -> None:
            committed = batch.take_outcomes(flush=flush)
            for item in committed:
                if item.stage == "persist" and not item.complete:
                    outcomes.append(item)
                    if item.accession not in failed_accessions:
                        counters["failed"] += 1
                        failed_accessions.add(item.accession)
            if committed and emit and on_progress is not None:
                on_progress(snapshot())

        gen = iter_ownership_filings(
            zip_path, cache_root=zip_path.parent, policy=self._policy, ciks=ciks
        )
        try:
            for meta in gen:
                if cancelled():
                    interrupted = True
                    break
                discovered += 1
                accession = meta.accession_number
                if accession in attempted or ledger.is_terminal(accession):
                    skipped_resume += 1
                    continue
                try:
                    row = bulk_metadata_to_index_row(meta)
                except SecBulkError:
                    skipped_metadata += 1
                    continue
                attempted.add(accession)
                try:
                    outcome = process_filing_row(
                        row,
                        client=self._client,
                        cache_root=self._cache_root,
                        counters=counters,
                        policy=self._policy,
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

        if (
            self._cleanup
            and not interrupted
            and not any(not item.complete for item in outcomes)
        ):
            purge_stale_cache(self._cache_root, policy=self._policy)

        summary = snapshot()
        if on_progress is not None:
            on_progress(summary)
        return summary
