"""Bounded SEC writes, with filing outcomes published only after commit."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from insider_scanner.core.models import InsiderTrade
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.repositories import UpsertResult
from insider_scanner.services.sec_downloads import SecFilingOutcome, SecFilingStatus
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_persistence")


class SecTradeBatch:
    """Buffer mapped filings without holding a transaction during downloads.

    A failed transaction leaves every filing retryable. Row rejections retain
    committed good rows, but leave their containing filing retryable as well.
    Callers must drain outcomes before recording checkpoints or date coverage.
    """

    def __init__(
        self,
        upsert: Callable[[Iterable[InsiderTrade]], UpsertResult],
        *,
        max_filings: int = 200,
        max_rows: int = 5_000,
    ) -> None:
        if max_filings < 1 or max_rows < 1:
            raise ValueError("SEC batch limits must be positive")
        self._upsert = upsert
        self._max_filings = max_filings
        self._max_rows = max_rows
        self._filings: list[tuple[str | None, int, int]] = []
        self._trades: list[InsiderTrade] = []
        self._outcomes: list[SecFilingOutcome] = []
        self.upserts = UpsertResult()

    def add(self, accession: str | None, trades: Sequence[InsiderTrade]) -> None:
        if self._filings and len(self._trades) + len(trades) > self._max_rows:
            self.flush()
        start = len(self._trades)
        self._trades.extend(trades)
        self._filings.append((accession, start, len(self._trades)))
        if (
            len(self._filings) >= self._max_filings
            or len(self._trades) >= self._max_rows
        ):
            self.flush()

    def flush(self) -> None:
        if not self._filings:
            return
        filings, trades = self._filings, self._trades
        self._filings, self._trades = [], []
        try:
            result = self._upsert(trades) if trades else UpsertResult()
        except PersistenceError as error:
            reason = str(error) or type(error).__name__
            for accession, _, _ in filings:
                self._record(accession, reason)
            return
        self.upserts += result
        for accession, start, end in filings:
            errors = [
                error.message for error in result.errors if start <= error.index < end
            ]
            self._record(accession, "; ".join(errors))

    def _record(self, accession: str | None, reason: str) -> None:
        if reason:
            # Errors from the repository exclude SQL parameter payloads.
            reason = "".join(char if char.isprintable() else "?" for char in reason)
            _log.warning(
                "SEC batch persist failed accession=%s reason=%s", accession, reason
            )
        self._outcomes.append(
            SecFilingOutcome(
                accession,
                SecFilingStatus.RETRYABLE_FAILURE
                if reason
                else SecFilingStatus.SUCCESS,
                "persist" if reason else "",
                reason,
            )
        )

    def take_outcomes(self, *, flush: bool = False) -> tuple[SecFilingOutcome, ...]:
        if flush:
            self.flush()
        outcomes, self._outcomes = self._outcomes, []
        return tuple(outcomes)
