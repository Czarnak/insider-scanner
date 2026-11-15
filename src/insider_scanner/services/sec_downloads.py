"""SEC daily index orchestration with invocation-local counters.

The database ingestion service supplies durable filing and date resume state.
Standalone downloads report only work attempted by the current invocation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from insider_scanner.core import edgar, sec_index
from insider_scanner.core.sec_client import (
    SecBlockedError,
    SecClient,
    SecFilingNotFoundError,
)
from insider_scanner.core.sec_downloader import purge_stale_cache
from insider_scanner.core.sec_ingestion import (
    _COUNTER_KEYS,
    _RECOVERABLE,
    SecFilingOutcome,
    SecFilingStatus,
    _accession_from_path,
    _failure_reason,
    _log_recoverable_level,
    new_counters,
    process_filing_row,
)
from insider_scanner.core.sec_ownership_parser import OwnershipFiling
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceProfile,
    SecSecurityPolicy,
)
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.services.common import not_cancelled, validate_range
from insider_scanner.utils.logging import get_logger

__all__ = [
    "SecDownloadProgress",
    "SecDownloadReport",
    "SecFilingOutcome",
    "SecFilingStatus",
    "download_filings_in_range",
    "new_counters",
    "process_filing_row",
    "sec_today",
]

_log = get_logger("sec_downloads")


def sec_today() -> date:
    """Return the filing calendar date at the SEC's US Eastern location."""
    return datetime.now(ZoneInfo("America/New_York")).date()


@dataclass(frozen=True, slots=True)
class SecDownloadProgress:
    """Cumulative snapshot emitted after each completed date."""

    current_date: date
    dates_completed: int
    dates_total: int
    discovered: int
    downloaded: int
    parsed: int
    skipped: int
    failed: int
    unsupported: int = 0


@dataclass(frozen=True, slots=True)
class SecDownloadReport:
    """Final aggregate result of a download run.

    The counters are independent tallies, not a partition: ``downloaded``
    and ``skipped`` record how bytes were obtained (network vs. fresh cache),
    while ``parsed`` and ``failed`` record the parse outcome. A filing that is
    fetched (or cache-served) and then fails to parse is therefore counted in
    both its source bucket and ``failed`` — do not sum the buckets as a total.
    """

    interval: DateInterval
    dates_total: int
    dates_completed: int
    discovered: int
    downloaded: int
    parsed: int
    skipped: int
    failed: int
    unsupported: int = 0
    outcomes: tuple[SecFilingOutcome, ...] = ()
    cancelled: bool = False
    stopped_on_error: bool = False


def download_filings_in_range(
    *,
    start_date: date,
    end_date: date,
    client: SecClient,
    cache_root: Path,
    on_progress: Callable[[SecDownloadProgress], None] | None = None,
    on_filing_parsed: Callable[[sec_index.SecMasterIndexRow, OwnershipFiling], None]
    | None = None,
    on_date_flush: Callable[[], tuple[SecFilingOutcome, ...]] | None = None,
    should_skip: Callable[[str], bool] | None = None,
    on_filing_outcome: Callable[[SecFilingOutcome], None] | None = None,
    cancelled: Callable[[], bool] = not_cancelled,
    continue_on_error: bool = True,
    cleanup: bool = True,
    retain_failed_downloads: bool = False,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
    clock: Callable[[], date] = sec_today,
) -> SecDownloadReport:
    """Download + parse every ownership filing across an inclusive date range.

    Returns a :class:`SecDownloadReport`; never writes to the database.
    Counters start at zero on every call. Durable resume is supplied through
    ``should_skip`` by the database ingestion service.

    A buffered filing sink may supply ``on_date_flush`` to commit pending rows
    before date progress is emitted. It returns one retryable
    failure outcome per filing that did not persist. The hook also runs after a
    partial date, cancellation or an exception, preserving valid pending rows
    without marking the affected date complete.
    """
    if type(client) is not SecClient:
        raise TypeError("client must be exactly SecClient")
    if not isinstance(cache_root, Path):
        raise TypeError("cache_root must be a pathlib.Path")
    interval = validate_range(start_date, end_date)
    today = clock()
    if interval.end > today:
        raise ValueError("end_date must not be in the future")
    all_dates = _dates_in(interval)

    completed: set[date] = set()
    counters = new_counters()
    interrupted = False
    stopped_on_error = False
    outcomes: list[SecFilingOutcome] = []

    for day in all_dates:
        if cancelled():
            interrupted = True
            break
        try:
            outcome = _process_date(
                day,
                client=client,
                cache_root=cache_root,
                counters=counters,
                cancelled=cancelled,
                retain_failed_downloads=retain_failed_downloads,
                policy=policy,
                on_filing_parsed=on_filing_parsed,
                outcomes=outcomes,
                today=today,
                should_skip=should_skip,
                on_filing_outcome=on_filing_outcome,
            )
        except BaseException:
            if on_date_flush is not None:
                try:
                    on_date_flush()
                except BaseException as flush_error:  # noqa: BLE001 - preserve the original abort
                    # Preserve the original block/interruption even if cleanup
                    # fails. Pending accessions remain eligible for the retry.
                    _log.warning(
                        "SEC pending filing flush failed while aborting exception=%s",
                        type(flush_error).__name__,
                    )
            raise
        if on_date_flush is not None:
            flush_failures = on_date_flush()
            outcomes.extend(flush_failures)
            counters["failed"] += len(flush_failures)
            if flush_failures and outcome is not None:
                outcome = False
        if outcome is None:  # cancelled mid-date — leave the date uncovered
            interrupted = True
            break
        if outcome:
            completed.add(day)
            _emit_progress(on_progress, day, len(completed), len(all_dates), counters)
        elif not continue_on_error:
            stopped_on_error = True
            break

    fully_done = len(completed) >= len(all_dates) and not interrupted
    if fully_done and cleanup:
        purge_stale_cache(cache_root, policy=policy)

    return SecDownloadReport(
        interval=interval,
        dates_total=len(all_dates),
        dates_completed=len(completed),
        outcomes=tuple(outcomes),
        cancelled=interrupted,
        stopped_on_error=stopped_on_error,
        **{key: counters[key] for key in _COUNTER_KEYS},
    )


def _process_date(
    day: date,
    *,
    client: SecClient,
    cache_root: Path,
    counters: dict[str, int],
    cancelled: Callable[[], bool],
    retain_failed_downloads: bool,
    policy: SecSecurityPolicy,
    on_filing_parsed: Callable[[sec_index.SecMasterIndexRow, OwnershipFiling], None]
    | None = None,
    outcomes: list[SecFilingOutcome] | None = None,
    today: date,
    should_skip: Callable[[str], bool] | None = None,
    on_filing_outcome: Callable[[SecFilingOutcome], None] | None = None,
) -> bool | None:
    """Process one calendar day.

    Returns True when the date is fully handled (including an empty filing day),
    False on a date-level failure (so a resume retries it), or None if cancelled
    partway through the day.
    """
    url = edgar.build_daily_master_index_url(day)
    try:
        index_text = client.fetch_text(url, profile=SecResourceProfile.DAILY_INDEX)
    except SecFilingNotFoundError:
        _log.info("SEC daily index absent stage=index date=%s", day.isoformat())
        if day >= today:
            if outcomes is not None:
                outcomes.append(
                    SecFilingOutcome(
                        None,
                        SecFilingStatus.RETRYABLE_FAILURE,
                        "index",
                        "index_not_published",
                    )
                )
            return False
        return True
    except SecBlockedError:
        raise
    except _RECOVERABLE as exc:
        _log_recoverable_level(
            "SEC daily index failed stage=index date=%s reason=%s",
            day.isoformat(),
            exc,
        )
        if outcomes is not None:
            outcomes.append(
                SecFilingOutcome(
                    None,
                    SecFilingStatus.RETRYABLE_FAILURE,
                    "index",
                    _failure_reason(exc),
                )
            )
        return False

    rows = sec_index.parse_master_index(index_text)
    counters["discovered"] += len(rows)
    any_retryable_failure = False
    for row in rows:
        if cancelled():
            return None
        accession = _accession_from_path(row.archive_path)
        if accession is not None and should_skip is not None and should_skip(accession):
            counters["skipped"] += 1
            continue
        try:
            outcome = process_filing_row(
                row,
                client=client,
                cache_root=cache_root,
                counters=counters,
                retain_failed_downloads=retain_failed_downloads,
                policy=policy,
                on_filing_parsed=on_filing_parsed,
            )
        except SecBlockedError:
            if on_filing_outcome is not None:
                on_filing_outcome(
                    SecFilingOutcome(
                        accession,
                        SecFilingStatus.RETRYABLE_FAILURE,
                        "fetch",
                        "SecBlockedError",
                    )
                )
            raise
        if outcome.status is not SecFilingStatus.SUCCESS:
            if on_filing_outcome is not None:
                on_filing_outcome(outcome)
            if outcomes is not None:
                outcomes.append(outcome)
        if not outcome.complete:
            any_retryable_failure = True
    if day >= today:
        # Persist useful rows from an available current-day index, but retry the
        # date after it ends because the index can still acquire filings.
        if outcomes is not None:
            outcomes.append(
                SecFilingOutcome(
                    None,
                    SecFilingStatus.RETRYABLE_FAILURE,
                    "index",
                    "index_not_final",
                )
            )
        return False
    return not any_retryable_failure


def _dates_in(interval: DateInterval) -> tuple[date, ...]:
    span = (interval.end - interval.start).days
    return tuple(interval.start + timedelta(days=offset) for offset in range(span + 1))


def _emit_progress(
    on_progress: Callable[[SecDownloadProgress], None] | None,
    current_date: date,
    dates_completed: int,
    dates_total: int,
    counters: dict[str, int],
) -> None:
    if on_progress is None:
        return
    on_progress(
        SecDownloadProgress(
            current_date=current_date,
            dates_completed=dates_completed,
            dates_total=dates_total,
            **{key: counters[key] for key in _COUNTER_KEYS},
        )
    )
