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

* ``SecTradeMappingError`` — logged with the accession and propagated so a
  later run with repaired mapping logic can retry the filing.
* :class:`PersistenceError` (transient DB issue) — propagated. The downloader
  hook catches it, counts a ``failed``, and leaves the date uncovered so the next
  run retries it. Already-persisted rows from the partial date are harmless
  because re-ingest is idempotent (SEC canonical-key identity).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
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
from insider_scanner.persistence.coverage import DateInterval, normalize_intervals
from insider_scanner.persistence.repositories import UpsertResult
from insider_scanner.services.common import not_cancelled, validate_range
from insider_scanner.services.context import PersistenceContext
from insider_scanner.services.sec_downloads import (
    SecDownloadProgress,
    SecDownloadReport,
    SecFilingOutcome,
    download_filings_in_range,
    sec_today,
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

    ``failures`` counts fetch/parse/mapping/persistence failures; optional cache
    writes are best-effort and unsupported legacy documents have a separate
    counter. ``outcomes`` identifies rejected or retryable filings.

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
    unsupported: int = 0
    outcomes: tuple[SecFilingOutcome, ...] = ()
    cancelled: bool = False
    stopped_on_error: bool = False


@dataclass(slots=True)
class _RunState:
    """Mutable accumulators for one ingest run (never escapes the call)."""

    upserts: UpsertResult = field(default_factory=UpsertResult)


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
        self._clock = clock or sec_today
        self._cleanup = cleanup
        self._continue_on_error = continue_on_error
        self._checkpoint_path = checkpoint_path

    def ingest_date(
        self,
        day: date,
        *,
        cancelled: Callable[[], bool] = not_cancelled,
        on_progress: Callable[[SecDownloadProgress], None] | None = None,
    ) -> SecDailyIngestionSummary:
        """Ingest a single calendar day (equivalent to a one-day range)."""
        return self.ingest_range(day, day, cancelled=cancelled, on_progress=on_progress)

    def ingest_range(
        self,
        start: date,
        end: date,
        *,
        cancelled: Callable[[], bool] = not_cancelled,
        on_progress: Callable[[SecDownloadProgress], None] | None = None,
    ) -> SecDailyIngestionSummary:
        """Ingest an inclusive date range, processing only uncovered gaps.

        ``on_progress`` (optional) receives one :class:`SecDownloadProgress`
        snapshot per fully completed date. It is invoked *after* the date's
        coverage is recorded, so a failing user callback can never lose coverage.
        """
        requested = validate_range(start, end)
        today = self._clock()
        if requested.end > today:
            raise ValueError("end_date must not be in the future")
        dates_total = _day_count(requested)

        state = _RunState()
        sink = self._make_sink(state)
        progress = _compose_progress(self._make_coverage_recorder(), on_progress)

        gaps = self._persistence.coverage.gaps(
            _COVERAGE_DOMAIN, SEC_DAILY_IDENTIFIER, _COVERAGE_SOURCE, requested
        )
        if requested.start <= today <= requested.end:
            gaps = normalize_intervals([*gaps, DateInterval(today, today)])

        filings_discovered = 0
        filings_parsed = 0
        download_failed = 0
        gap_dates_completed = 0
        total_gap_days = sum(_day_count(gap) for gap in gaps)
        outcomes: list[SecFilingOutcome] = []
        unsupported = 0
        was_cancelled = False
        stopped_on_error = False
        for gap in gaps:
            if cancelled():
                was_cancelled = True
                break
            report = self._download_gap(
                gap, sink=sink, on_progress=progress, cancelled=cancelled
            )
            filings_discovered += report.discovered
            filings_parsed += report.parsed
            download_failed += report.failed
            gap_dates_completed += report.dates_completed
            unsupported += report.unsupported
            outcomes.extend(report.outcomes)
            was_cancelled = report.cancelled
            stopped_on_error = report.stopped_on_error
            if was_cancelled or stopped_on_error:
                break

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
            failures=download_failed,
            unsupported=unsupported,
            outcomes=tuple(outcomes),
            cancelled=was_cancelled,
            stopped_on_error=stopped_on_error,
        )

    def _download_gap(
        self,
        gap: DateInterval,
        *,
        sink: Callable[[SecMasterIndexRow, OwnershipFiling], None],
        on_progress: Callable[[SecDownloadProgress], None],
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
            on_progress=on_progress,
            clock=self._clock,
        )

    def _make_sink(
        self, state: _RunState
    ) -> Callable[[SecMasterIndexRow, OwnershipFiling], None]:
        """Build the per-run persist sink closure.

        Mapping and persistence failures propagate so the downloader records the
        accession and leaves its date uncovered for a later retry.
        """

        def _sink(row: SecMasterIndexRow, filing: OwnershipFiling) -> None:
            try:
                trades = ownership_filing_to_trades(filing, row)
            except SecTradeMappingError as exc:
                _log.warning(
                    "SEC daily mapping failed accession=%s reason=%s",
                    _safe_for_log(filing.accession_number),
                    type(exc).__name__,
                )
                raise
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
                now=datetime.combine(self._clock(), time.min, tzinfo=UTC),
            )

        return _record_coverage


def _compose_progress(
    record_coverage: Callable[[SecDownloadProgress], None],
    on_progress: Callable[[SecDownloadProgress], None] | None,
) -> Callable[[SecDownloadProgress], None]:
    """Compose the coverage recorder with an optional user progress callback.

    Coverage is always recorded first; the user callback (if any) fires second.
    This ordering guarantees a failing user callback can never lose the coverage
    bookkeeping for an already-completed date.
    """
    if on_progress is None:
        return record_coverage

    def _composed(snapshot: SecDownloadProgress) -> None:
        record_coverage(snapshot)
        on_progress(snapshot)

    return _composed


def _day_count(interval: DateInterval) -> int:
    return (interval.end - interval.start).days + 1
