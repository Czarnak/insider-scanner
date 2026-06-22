"""Idempotent SEC daily ingestion: download → map → persist with coverage gating.

Wraps the resumable downloader (:func:`download_filings_in_range`) with a persist
sink and per-date coverage bookkeeping so that re-running the same daily ingest
does not duplicate records. Each successfully parsed ownership filing is mapped to
``InsiderTrade`` rows and upserted; coverage is recorded only for dates that fully
complete, so an interruption (cancel, transient DB failure) leaves the affected
date uncovered and a follow-up run retries exactly those dates.

Error model
-----------
The persist sink distinguishes two failure classes:

* ``SecTradeMappingError`` (malformed filing) — swallowed at filing level. A
  malformed filing fails mapping deterministically on every retry, so triggering
  a date-level retry would be a poison pill. The filing is logged, counted in
  ``mapping_failures``, and skipped; the date still completes.
* :class:`PersistenceError` (transient DB issue) — propagated. The downloader
  hook catches it, counts a ``failed``, and leaves the date uncovered so the next
  run retries it. Already-persisted rows from the partial date are harmless
  because re-ingest is idempotent (SEC canonical-key identity).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from insider_scanner.core.sec_client import SecClient
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
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.persistence.repositories import UpsertResult
from insider_scanner.services.common import not_cancelled, validate_range
from insider_scanner.services.context import PersistenceContext
from insider_scanner.services.sec_downloads import (
    SecDownloadProgress,
    SecDownloadReport,
    download_filings_in_range,
)
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_daily")

_CONTROL_CHARS_RE = re.compile(r"[^\x20-\x7E]")


def _safe_for_log(value: str | None) -> str:
    """Sanitize a string for log output by replacing non-printable ASCII with '?'."""
    return _CONTROL_CHARS_RE.sub("?", value) if value else ""

# Sentinel coverage identifier for the SEC daily feed. It cannot collide with a
# real ticker (no ticker contains the double-underscore sentinel form), so daily
# coverage is independent of per-ticker coverage.
SEC_DAILY_IDENTIFIER = "__sec_daily__"
_COVERAGE_DOMAIN = "us"
_COVERAGE_SOURCE = "sec_edgar"


@dataclass(frozen=True, slots=True)
class SecDailyIngestionSummary:
    """Aggregate result of one ingest run.

    ``failures`` = ``download_failed`` + ``mapping_failures``. The two never
    overlap: ``download_failed`` counts fetch/parse/promote failures and any
    :class:`PersistenceError` the sink raised and the downloader caught;
    ``mapping_failures`` counts ``SecTradeMappingError``s the sink swallowed,
    which are never reflected in the downloader's ``failed`` tally.

    Note: ``filings_parsed`` counts every successfully-parsed filing handed to
    the persist sink, including any whose sink later raised a
    :class:`PersistenceError`; ``filings_parsed`` and ``failures`` therefore do
    not form a partition of ``filings_discovered``.
    """

    interval: DateInterval
    dates_total: int
    dates_completed: int
    filings_discovered: int
    filings_parsed: int
    transactions_inserted: int
    transactions_updated: int
    transactions_skipped: int
    failures: int


@dataclass(slots=True)
class _RunState:
    """Mutable accumulators for one ingest run (never escapes the call)."""

    upserts: UpsertResult = UpsertResult()
    mapping_failures: int = 0


class SecDailyIngestionService:
    """Download, map, and persist SEC ownership filings with coverage gating."""

    def __init__(
        self,
        persistence: PersistenceContext,
        *,
        client: SecClient,
        cache_root: Path,
        policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
        clock: Callable[[], date] | None = None,
        cleanup: bool = True,
        continue_on_error: bool = True,
        checkpoint_path: Path | None = None,
    ) -> None:
        self._persistence = persistence
        self._client = client
        self._cache_root = cache_root
        self._policy = policy
        # ``clock`` is reserved for future use (e.g. defaulting ranges to "today").
        # It is stored but currently unused by the ingestion logic.
        self._clock = clock
        self._cleanup = cleanup
        self._continue_on_error = continue_on_error
        self._checkpoint_path = checkpoint_path

    def ingest_date(
        self,
        day: date,
        *,
        cancelled: Callable[[], bool] = not_cancelled,
    ) -> SecDailyIngestionSummary:
        """Ingest a single calendar day (equivalent to a one-day range)."""
        return self.ingest_range(day, day, cancelled=cancelled)

    def ingest_range(
        self,
        start: date,
        end: date,
        *,
        cancelled: Callable[[], bool] = not_cancelled,
    ) -> SecDailyIngestionSummary:
        """Ingest an inclusive date range, processing only uncovered gaps."""
        requested = validate_range(start, end)
        dates_total = _day_count(requested)

        state = _RunState()
        sink = self._make_sink(state)
        record_coverage = self._make_coverage_recorder()

        gaps = self._persistence.coverage.gaps(
            _COVERAGE_DOMAIN, SEC_DAILY_IDENTIFIER, _COVERAGE_SOURCE, requested
        )

        filings_discovered = 0
        filings_parsed = 0
        download_failed = 0
        gap_dates_completed = 0
        total_gap_days = 0
        for gap in gaps:
            if cancelled():
                break
            total_gap_days += _day_count(gap)
            report = self._download_gap(
                gap, sink=sink, record_coverage=record_coverage, cancelled=cancelled
            )
            filings_discovered += report.discovered
            filings_parsed += report.parsed
            download_failed += report.failed
            gap_dates_completed += report.dates_completed

        already_covered_days = dates_total - total_gap_days
        dates_completed = already_covered_days + gap_dates_completed

        return SecDailyIngestionSummary(
            interval=requested,
            dates_total=dates_total,
            dates_completed=dates_completed,
            filings_discovered=filings_discovered,
            filings_parsed=filings_parsed,
            transactions_inserted=state.upserts.inserted,
            transactions_updated=state.upserts.updated,
            transactions_skipped=state.upserts.skipped,
            failures=download_failed + state.mapping_failures,
        )

    def _download_gap(
        self,
        gap: DateInterval,
        *,
        sink: Callable[[SecMasterIndexRow, OwnershipFiling], None],
        record_coverage: Callable[[SecDownloadProgress], None],
        cancelled: Callable[[], bool],
    ) -> SecDownloadReport:
        return download_filings_in_range(
            start_date=gap.start,
            end_date=gap.end,
            client=self._client,
            cache_root=self._cache_root,
            policy=self._policy,
            cleanup=self._cleanup,
            continue_on_error=self._continue_on_error,
            checkpoint_path=self._checkpoint_path,
            cancelled=cancelled,
            on_filing_parsed=sink,
            on_progress=record_coverage,
        )

    def _make_sink(
        self, state: _RunState
    ) -> Callable[[SecMasterIndexRow, OwnershipFiling], None]:
        """Build the per-run persist sink closure.

        ``SecTradeMappingError`` is swallowed (filing-level skip). Any
        :class:`PersistenceError` from ``upsert`` propagates so the downloader
        treats it as a date-level retry.
        """

        def _sink(row: SecMasterIndexRow, filing: OwnershipFiling) -> None:
            try:
                trades = ownership_filing_to_trades(filing, row)
            except SecTradeMappingError as exc:
                state.mapping_failures += 1
                _log.warning(
                    "SEC daily mapping failed accession=%s reason=%s",
                    _safe_for_log(filing.accession_number),
                    type(exc).__name__,
                )
                return
            result = self._persistence.us_trades.upsert(trades)
            state.upserts = state.upserts + result

        return _sink

    def _make_coverage_recorder(
        self,
    ) -> Callable[[SecDownloadProgress], None]:
        """Build the per-date coverage recorder closure.

        ``on_progress`` fires only after a date is fully completed, so this
        records exactly the completed dates; ``coverage.add`` compacts
        consecutive days.
        """

        def _record_coverage(snapshot: SecDownloadProgress) -> None:
            self._persistence.coverage.add(
                _COVERAGE_DOMAIN,
                SEC_DAILY_IDENTIFIER,
                _COVERAGE_SOURCE,
                DateInterval(snapshot.current_date, snapshot.current_date),
            )

        return _record_coverage


def _day_count(interval: DateInterval) -> int:
    return (interval.end - interval.start).days + 1
