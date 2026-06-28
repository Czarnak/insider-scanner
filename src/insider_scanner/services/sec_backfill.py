"""Explicit, resumable full bulk backfill from a SEC submissions ZIP.

Streams ownership-filing metadata from a user-supplied submissions archive,
synthesizes a SecMasterIndexRow per filing, and runs each through the *same*
hardened download/parse/persist pipeline as daily ingestion. Resume state is a
JSON checkpoint of completed accession numbers keyed to the ZIP identity.

Known limitation: the checkpoint stores the full set of completed accessions; for
a whole-archive backfill this set can grow large. Acceptable for fixture-scale and
repair runs; a compact cursor is a future optimization (do not silently cap it).

Resume convergence: a filing that fails BEFORE the persist sink (fetch 404, a
document that never parses as valid ownership XML, or a security-bound rejection)
is intentionally retried on every resume run -- it is never added to the completed
set. This guarantees transient failures are retried, but a *deterministic*
non-mapping failure is therefore re-fetched on each run and never converges
(bounded, no data loss). Distinguishing deterministic from transient failures
(e.g. a separate failed_accessions checkpoint set) is a deferred follow-up.
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
from insider_scanner.core.sec_client import SecClient
from insider_scanner.core.sec_downloader import purge_stale_cache
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ownership_parser import OwnershipFiling
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityPolicy,
)
from insider_scanner.core.sec_trade_mapping import (
    SecTradeMappingError,
    ownership_filing_to_trades,
)
from insider_scanner.persistence.json_state import atomic_write_json, read_json_dict
from insider_scanner.persistence.repositories import UpsertResult
from insider_scanner.services.common import not_cancelled
from insider_scanner.services.context import PersistenceContext
from insider_scanner.services.sec_downloads import new_counters, process_filing_row
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_backfill")
_CHECKPOINT_VERSION = 1


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


class SecBackfillService:
    def __init__(
        self,
        persistence: PersistenceContext,
        *,
        client: SecClient,
        cache_root: Path,
        policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
        cleanup: bool = True,
        checkpoint_path: Path | None = None,
    ) -> None:
        self._persistence = persistence
        self._client = client
        self._cache_root = cache_root
        self._policy = policy
        self._cleanup = cleanup
        self._checkpoint_path = checkpoint_path

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
        upserts = UpsertResult()
        mapping_failures = 0
        skipped_resume = 0
        skipped_metadata = 0
        discovered = 0

        completed = self._load_completed(zip_path)
        gen = iter_ownership_filings(
            zip_path, cache_root=zip_path.parent, policy=self._policy, ciks=ciks
        )
        interrupted = False
        try:
            for meta in gen:
                if cancelled():
                    interrupted = True
                    break
                discovered += 1
                if meta.accession_number in completed:
                    skipped_resume += 1
                    continue
                try:
                    row = bulk_metadata_to_index_row(meta)
                except SecBulkError:
                    skipped_metadata += 1
                    continue

                reached_sink = [False]

                def _sink(r: SecMasterIndexRow, filing: OwnershipFiling) -> None:
                    nonlocal upserts, mapping_failures
                    reached_sink[0] = True
                    try:
                        trades = ownership_filing_to_trades(filing, r)
                    except SecTradeMappingError:
                        mapping_failures += 1
                        return
                    upserts = upserts + self._persistence.us_trades.upsert(trades)

                ok = process_filing_row(
                    row,
                    client=self._client,
                    cache_root=self._cache_root,
                    counters=counters,
                    policy=self._policy,
                    on_filing_parsed=_sink,
                )
                if ok and reached_sink[0]:
                    completed.add(meta.accession_number)
                    self._save_completed(zip_path, completed)
        finally:
            gen.close()  # release the ZIP handle promptly on early exit

        fully_done = not interrupted
        if fully_done:
            self._delete_checkpoint()
            if self._cleanup:
                purge_stale_cache(self._cache_root, policy=self._policy)

        summary = SecBackfillSummary(
            filings_discovered=discovered,
            filings_parsed=counters["parsed"],
            transactions_inserted=upserts.inserted,
            transactions_updated=upserts.updated,
            transactions_skipped=upserts.skipped,
            skipped_resume=skipped_resume,
            skipped_metadata=skipped_metadata,
            failures=counters["failed"] + mapping_failures,
        )
        if on_progress is not None:
            on_progress(summary)
        return summary

    # --- checkpoint (resume state) -----------------------------------------

    def _load_completed(self, zip_path: Path) -> set[str]:
        if self._checkpoint_path is None:
            return set()
        data = read_json_dict(self._checkpoint_path)
        if not data or data.get("version") != _CHECKPOINT_VERSION:
            return set()
        if data.get("zip") != zip_path.name:
            return set()  # different archive -> start fresh
        raw = data.get("completed_accessions")
        return (
            {a for a in raw if isinstance(a, str)} if isinstance(raw, list) else set()
        )

    def _save_completed(self, zip_path: Path, completed: set[str]) -> None:
        if self._checkpoint_path is None:
            return
        try:
            atomic_write_json(
                self._checkpoint_path,
                {
                    "version": _CHECKPOINT_VERSION,
                    "zip": zip_path.name,
                    "completed_accessions": sorted(completed),
                },
            )
        except OSError:
            _log.warning("SEC backfill checkpoint save failed")

    def _delete_checkpoint(self) -> None:
        if self._checkpoint_path is None:
            return
        try:
            self._checkpoint_path.unlink(missing_ok=True)
        except OSError:
            _log.warning("SEC backfill checkpoint cleanup failed")
