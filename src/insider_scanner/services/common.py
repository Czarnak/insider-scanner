"""Shared scan-service contracts and validation."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Protocol, TypeVar

from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult, FetchStatus
from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.senate import flag_congress_trades
from insider_scanner.persistence.coverage import DateInterval, normalize_intervals
from insider_scanner.persistence.refresh import is_fresh
from insider_scanner.persistence.repositories import UpsertResult
from insider_scanner.services.context import PersistenceContext
from insider_scanner.utils.config import DEFAULT_CACHE_TTL as CACHE_TTL_SECONDS

DEFAULT_CACHE_TTL = timedelta(seconds=CACHE_TTL_SECONDS)
# Latest endpoints refresh a recent overlapping tail to absorb late filings.
LATEST_OVERLAP_COUNT = 200
# Revisit recent publication dates, independent of the underlying trade date.
RECENT_FILING_DAYS = 7

TradeT = TypeVar("TradeT")
CancellationCheck = Callable[[], bool]


class BoundedSourceAdapter(Protocol[TradeT]):
    def __call__(
        self,
        identifier: str | None,
        start_date: date,
        end_date: date,
        use_cache: bool,
    ) -> list[TradeT]: ...


class LatestSourceAdapter(Protocol[TradeT]):
    def __call__(
        self,
        count: int,
        use_cache: bool,
        start_date: date | None,
        end_date: date | None,
    ) -> list[TradeT]: ...


class ScanError(RuntimeError):
    """A source scan failed without marking its interval as covered."""


def source_issues(
    result: FetchResult[TradeT], *, source: str, identifier: str
) -> list[FetchIssue]:
    """Attach request context, including an incomplete result with no details."""
    issues = result.issues
    if not result.complete and not issues:
        issues = (
            FetchIssue(
                result.status.value,
                retryable=result.status != FetchStatus.UNSUPPORTED,
            ),
        )
    return [
        replace(
            issue,
            source=source,
            identifier=(
                f"{identifier}: {issue.identifier}" if issue.identifier else identifier
            ),
        )
        for issue in issues
    ]


def scan_result(records: list[TradeT], issues: list[FetchIssue]) -> FetchResult[TradeT]:
    status = None
    if (
        issues
        and not records
        and all(not issue.retryable for issue in issues)
        and not any(issue.reason.startswith("persistence_error:") for issue in issues)
    ):
        status = FetchStatus.UNSUPPORTED
    return FetchResult(records, issues=issues, status=status)


def persistence_issues(
    result: UpsertResult, *, source: str, identifier: str
) -> list[FetchIssue]:
    """Expose rejected input positions without claiming successful coverage."""
    return [
        FetchIssue(
            f"persistence_error: {error.message}",
            source,
            f"{identifier}: row {error.index}",
            retryable=error.retryable,
        )
        for error in result.errors
    ]


def normalize_identifier(value: str, label: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def validate_range(
    start_date: date | None,
    end_date: date | None,
) -> DateInterval:
    if start_date is None or end_date is None:
        raise ValueError("start_date and end_date must both be present")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    return DateInterval(start_date, end_date)


def subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def recent_filing_interval(
    requested: DateInterval, now: datetime
) -> DateInterval | None:
    start = max(requested.start, now.date() - timedelta(days=RECENT_FILING_DAYS - 1))
    return DateInterval(start, requested.end) if start <= requested.end else None


def bounded_refresh_mode(interval: DateInterval) -> str:
    return f"filing-tail:{interval.start}:{interval.end}"


def bounded_scan_gaps(
    persistence: PersistenceContext,
    domain: str,
    identifier: str,
    source: str,
    requested: DateInterval,
    *,
    now: datetime,
    ttl: timedelta,
    use_cache: bool,
) -> tuple[DateInterval, ...]:
    """Reuse history, but refresh recent filing dates after the bounded TTL."""
    if not use_cache:
        # A failed forced refresh must remain retryable on the next normal scan.
        persistence.refresh.clear(domain, identifier, source)
        persistence.coverage.remove(domain, identifier, source, requested)
        return (requested,)
    recent = recent_filing_interval(requested, now)
    if recent is None:
        return persistence.coverage.gaps(domain, identifier, source, requested)
    gaps: list[DateInterval] = []
    if requested.start < recent.start:
        historical = DateInterval(requested.start, recent.start - timedelta(days=1))
        gaps.extend(persistence.coverage.gaps(domain, identifier, source, historical))
    refreshed_at = persistence.refresh.get(
        domain, identifier, source, bounded_refresh_mode(recent)
    )
    if not is_fresh(refreshed_at, now=now, ttl=ttl):
        gaps.append(recent)
    return normalize_intervals(gaps)


def record_completed_scan(
    persistence: PersistenceContext,
    domain: str,
    identifier: str,
    source: str,
    requested: DateInterval,
    completed: DateInterval,
    *,
    now: datetime,
) -> None:
    persistence.coverage.add(domain, identifier, source, completed, now=now)
    recent = recent_filing_interval(requested, now)
    if (
        recent is not None
        and completed.start <= recent.start
        and completed.end >= recent.end
    ):
        persistence.refresh.set(
            domain, identifier, source, bounded_refresh_mode(recent), now
        )


def validate_sources(
    sources: Iterable[str],
    adapters: Mapping[str, object],
) -> tuple[str, ...]:
    source_values = (sources,) if isinstance(sources, str) else sources
    normalized = tuple(
        dict.fromkeys(source.strip().lower() for source in source_values)
    )
    if not normalized or any(not source for source in normalized):
        raise ValueError("sources must not be empty")
    unknown = [source for source in normalized if source not in adapters]
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(unknown)}")
    return normalized


def flag_congress_copies(trades: list[InsiderTrade]) -> list[InsiderTrade]:
    copies = [replace(trade) for trade in trades]
    return flag_congress_trades(copies)


def not_cancelled() -> bool:
    return False


def latest_refresh_mode(capacity: int) -> str:
    if capacity < 1:
        raise ValueError("capacity must be positive")
    return f"latest:{capacity}"
