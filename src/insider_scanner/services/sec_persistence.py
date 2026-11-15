"""Bounded, atomic SEC trade and ingestion-ledger writes."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ingestion import SecFilingOutcome, SecFilingStatus
from insider_scanner.core.sec_ownership_parser import OwnershipFiling
from insider_scanner.core.sec_trade_mapping import ownership_filing_to_trades
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.repositories import UpsertResult
from insider_scanner.persistence.sec_ingestion import (
    SecIngestionCommit,
    SecIngestionWrite,
)
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_persistence")


class SecTradeBatch:
    """Buffer filings without holding a transaction during network requests.

    The repository commits trades and their ledger entries together. A failed
    commit leaves every buffered accession retryable, including empty filings.
    """

    def __init__(
        self,
        commit: Callable[[Sequence[SecIngestionWrite]], SecIngestionCommit],
        *,
        max_filings: int = 200,
        max_rows: int = 5_000,
    ) -> None:
        if max_filings < 1 or max_rows < 1:
            raise ValueError("SEC batch limits must be positive")
        self._commit = commit
        self._max_filings = max_filings
        self._max_rows = max_rows
        self._entries: list[SecIngestionWrite] = []
        self._rows = 0
        self._outcomes: list[SecFilingOutcome] = []
        self.upserts = UpsertResult()

    def add(self, accession: str | None, trades: Sequence[InsiderTrade]) -> None:
        self._add(
            SecIngestionWrite(
                SecFilingOutcome(accession, SecFilingStatus.SUCCESS), tuple(trades)
            )
        )

    def add_outcome(self, outcome: SecFilingOutcome) -> None:
        self._add(SecIngestionWrite(outcome))

    def _add(self, entry: SecIngestionWrite) -> None:
        if self._entries and self._rows + len(entry.trades) > self._max_rows:
            self.flush()
        self._entries.append(entry)
        self._rows += len(entry.trades)
        if len(self._entries) >= self._max_filings or self._rows >= self._max_rows:
            self.flush()

    def flush(self) -> None:
        if not self._entries:
            return
        entries, self._entries = self._entries, []
        self._rows = 0
        try:
            result = self._commit(entries)
        except PersistenceError as error:
            reason = (
                "".join(char if char.isprintable() else "?" for char in str(error))
                or type(error).__name__
            )
            # A rolled-back database cannot retain the failed accession ledger.
            # Preserve each accession in the configured logs, with a stable
            # reason instead of arbitrary driver or exception text.
            for item in entries:
                _log.warning(
                    "SEC ingestion batch commit failed accession=%s stage=persist reason=%s",
                    item.outcome.accession,
                    type(error).__name__,
                )
            self._outcomes.extend(
                SecFilingOutcome(
                    item.outcome.accession,
                    SecFilingStatus.RETRYABLE_FAILURE,
                    "persist",
                    reason,
                )
                for item in entries
            )
            return
        self.upserts += UpsertResult(
            inserted=result.upserts.inserted,
            updated=result.upserts.updated,
            skipped=result.upserts.skipped,
        )
        self._outcomes.extend(result.outcomes)

    def take_outcomes(self, *, flush: bool = False) -> tuple[SecFilingOutcome, ...]:
        if flush:
            self.flush()
        outcomes, self._outcomes = self._outcomes, []
        return tuple(outcomes)


def make_persist_sink(
    batch: SecTradeBatch,
) -> Callable[[SecMasterIndexRow, OwnershipFiling], None]:
    """Map once through the shared sink; mapping failures remain retryable."""

    def sink(row: SecMasterIndexRow, filing: OwnershipFiling) -> None:
        accession = row.archive_path.rsplit("/", 1)[-1].removesuffix(".txt")
        batch.add(accession, ownership_filing_to_trades(filing, row))

    return sink
