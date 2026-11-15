"""Bulk SEC ingestion backed by the same durable ledger as daily ingestion.

Trades and filing status commit together in bounded batches. Ledger entries are
accession based, survive successful runs and archive renames, and are never
cleared by a targeted CIK run. Old JSON checkpoints are left untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from time import monotonic

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
from insider_scanner.persistence.sec_ingestion import SecArchiveProgress
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

    Live snapshots include observed parses before their batch commits.
    ``filings_committed`` counts successful filings whose batches committed;
    transaction counts also include only committed writes. ``members_completed``
    is the cumulative durable archive position, excluding retry members, across
    resumes. Filing counters and elapsed time describe this invocation. The ETA
    uses completed archive members, whose sizes can vary considerably.
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
    elapsed_seconds: float = 0.0
    filings_per_second: float = 0.0
    members_total: int = 0
    members_completed: int = 0
    eta_seconds: float | None = None
    filings_committed: int = 0


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
        since: date | None = None,
        until: date | None = None,
        force: bool = False,
        cancelled: Callable[[], bool] = not_cancelled,
        on_progress: Callable[[SecBackfillSummary], None] | None = None,
    ) -> SecBackfillSummary:
        if confirm is not True:
            raise SecBackfillConfirmationError(
                "Full bulk backfill requires explicit confirmation"
            )
        if since is not None and until is not None and since > until:
            raise ValueError("since must not be after until")
        if force and not ciks and (since is None or until is None):
            raise ValueError("Forced XML repair requires CIKs or both date bounds")

        counters = new_counters()
        started = monotonic()
        ledger = self._persistence.sec_ingestion
        members_total = 0

        def count_members(count: int) -> None:
            nonlocal members_total
            members_total = count

        archive_id = bulk_archive_fingerprint(
            zip_path, policy=self._policy, on_members=count_members
        )
        scope = (
            "full"
            if ciks is None
            else sha256(",".join(sorted(ciks)).encode("ascii")).hexdigest()
        )
        if since is not None or until is not None or force:
            scope = sha256(
                f"{scope}|{since}|{until}|force={force}".encode("ascii")
            ).hexdigest()
        # Explicit repairs reread their selected scope on every invocation.
        # Normal date/CIK scopes keep independent durable archive positions.
        position = (
            SecArchiveProgress()
            if force
            else ledger.archive_progress(archive_id, scope)
        )
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
        seen: set[str] = set()
        interrupted = False
        completed_members: list[int] = []
        retry_members: set[int] = set()
        accession_members: dict[str | None, int] = {}
        next_member = position.next_member
        durable_retries = set(position.retry_members)
        initial_completed = next_member - len(durable_retries)
        members_visited = 0
        last_progress_time = started
        last_progress_units = 0
        filings_committed = 0

        def cancellation_requested() -> bool:
            nonlocal interrupted
            interrupted = interrupted or cancelled()
            return interrupted

        def snapshot(*, final: bool = False) -> SecBackfillSummary:
            elapsed = max(0.0, monotonic() - started)
            members_completed = next_member - len(durable_retries)
            newly_completed = members_completed - initial_completed
            remaining = max(0, members_total - members_completed)
            eta = (
                0.0
                if remaining == 0
                else remaining * elapsed / newly_completed
                if newly_completed > 0
                else None
            )
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
                # The complete outcome list belongs to the final report. Do
                # not copy a growing failure list into every live snapshot.
                outcomes=tuple(outcomes) if final else (),
                cancelled=interrupted,
                discovered_rows=discovered_rows,
                duplicates=duplicates,
                skipped_members=skipped_members,
                elapsed_seconds=elapsed,
                filings_per_second=(len(attempted) - skipped_resume) / elapsed
                if elapsed > 0
                else 0.0,
                members_total=members_total,
                members_completed=members_completed,
                eta_seconds=eta,
                filings_committed=filings_committed,
            )

        def emit_progress(*, force: bool = False) -> None:
            nonlocal last_progress_time, last_progress_units
            if on_progress is None:
                return
            now = monotonic()
            units = discovered_rows + members_visited
            if (
                force
                or units - last_progress_units >= 100
                or now - last_progress_time >= 2.0
            ):
                on_progress(snapshot())
                last_progress_time = now
                last_progress_units = units

        def record_committed(*, flush: bool = False, emit: bool = True) -> None:
            nonlocal filings_committed
            committed = batch.take_outcomes(flush=flush)
            for item in committed:
                if item.status is SecFilingStatus.SUCCESS:
                    filings_committed += 1
                if not item.complete and item.accession in accession_members:
                    retry_members.add(accession_members[item.accession])
                if item.stage == "persist" and not item.complete:
                    outcomes.append(item)
                    if item.accession not in failed_accessions:
                        counters["failed"] += 1
                        failed_accessions.add(item.accession)
            if committed and emit:
                emit_progress(force=True)

        def checkpoint_members() -> None:
            nonlocal next_member
            if not completed_members:
                return
            record_committed(flush=True, emit=False)
            ledger.commit_archive_progress(
                archive_id,
                scope,
                [(index, index in retry_members) for index in completed_members],
            )
            next_member = max(next_member, max(completed_members) + 1)
            durable_retries.difference_update(completed_members)
            durable_retries.update(
                index for index in completed_members if index in retry_members
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
            nonlocal members_visited
            members_visited += 1
            completed_members.append(index)
            # Bound replay and write cost even for members with no filings.
            if len(completed_members) >= self._batch_filings:
                checkpoint_members()
            emit_progress()

        gen = iter_ownership_filings(
            zip_path,
            policy=self._policy,
            ciks=ciks,
            start_member=position.next_member,
            retry_members=position.retry_members,
            on_member_error=member_error,
            on_member_complete=member_complete,
            cancelled=cancellation_requested,
        )
        try:
            for meta in gen:
                if cancellation_requested():
                    break
                discovered_rows += 1
                if (since is not None or until is not None) and (
                    meta.filing_date is None
                    or (since is not None and meta.filing_date < since)
                    or (until is not None and meta.filing_date > until)
                ):
                    skipped_metadata += 1
                    emit_progress()
                    continue
                accession = meta.accession_number
                if accession in seen:
                    duplicates += 1
                else:
                    seen.add(accession)
                    discovered += 1
                if accession in attempted:
                    emit_progress()
                    continue
                if not force and ledger.is_terminal(accession):
                    attempted.add(accession)
                    skipped_resume += 1
                    emit_progress()
                    continue
                try:
                    row = bulk_metadata_to_index_row(meta)
                except SecBulkError:
                    skipped_metadata += 1
                    emit_progress()
                    continue
                attempted.add(accession)
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
                emit_progress()
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

        summary = snapshot(final=True)
        if on_progress is not None:
            on_progress(summary)
        return summary
