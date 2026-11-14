"""DB-first US insider scan service."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult, as_fetch_result
from insider_scanner.core.models import InsiderTrade
from insider_scanner.persistence.refresh import is_fresh
from insider_scanner.services.adapters import (
    default_us_adapters,
    default_us_latest_adapters,
)
from insider_scanner.services.common import (
    DEFAULT_CACHE_TTL,
    LATEST_OVERLAP_COUNT,
    BoundedSourceAdapter,
    LatestSourceAdapter,
    bounded_scan_gaps,
    flag_congress_copies,
    latest_refresh_mode,
    normalize_identifier,
    not_cancelled,
    record_completed_scan,
    scan_result,
    source_issues,
    subtract_months,
    validate_range,
    validate_sources,
)
from insider_scanner.services.context import PersistenceContext
from insider_scanner.services.sec_comparison import SEC_EDGAR_SOURCE


class UsScanService:
    def __init__(
        self,
        persistence: PersistenceContext,
        *,
        adapters: Mapping[str, BoundedSourceAdapter[InsiderTrade]] | None = None,
        latest_adapters: Mapping[str, LatestSourceAdapter[InsiderTrade]] | None = None,
        clock: Callable[[], datetime] | None = None,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        latest_overlap_count: int = LATEST_OVERLAP_COUNT,
    ) -> None:
        self._persistence = persistence
        self._adapters = dict(default_us_adapters() if adapters is None else adapters)
        self._latest_adapters = dict(
            default_us_latest_adapters() if latest_adapters is None else latest_adapters
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache_ttl = cache_ttl
        if latest_overlap_count < 1:
            raise ValueError("latest_overlap_count must be positive")
        self._latest_overlap_count = latest_overlap_count

    def scan(
        self,
        ticker: str,
        *,
        sources: Sequence[str],
        start_date: date | None,
        end_date: date | None,
        use_cache: bool = True,
        cancelled: Callable[[], bool] = not_cancelled,
    ) -> FetchResult[InsiderTrade]:
        identifier = normalize_identifier(ticker, "ticker").upper()
        now = self._clock()
        if start_date is None and end_date is None:
            end_date = now.date()
            start_date = subtract_months(end_date, 6)
        requested = validate_range(start_date, end_date)
        if requested.end > now.date():
            raise ValueError("end_date must not be in the future")
        selected = validate_sources(sources, self._bounded_sources_available())
        adapter_sources = tuple(
            source for source in selected if source in self._adapters
        )

        issues: list[FetchIssue] = []
        for source in adapter_sources:
            gaps = bounded_scan_gaps(
                self._persistence,
                "us",
                identifier,
                source,
                requested,
                now=now,
                ttl=self._cache_ttl,
                use_cache=use_cache,
            )
            for gap in gaps:
                if cancelled():
                    issues.append(FetchIssue("cancelled", source, identifier))
                    return scan_result(
                        self._query(identifier, selected, requested), issues
                    )
                context = f"{identifier} {gap.start}..{gap.end}"
                try:
                    fetched = as_fetch_result(
                        self._adapters[source](
                            identifier,
                            gap.start,
                            gap.end,
                            use_cache,
                        )
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    issues.append(FetchIssue(type(error).__name__, source, context))
                    continue
                if cancelled():
                    issues.append(FetchIssue("cancelled", source, context))
                    return scan_result(
                        self._query(identifier, selected, requested), issues
                    )
                trades = [
                    replace(trade, ticker=identifier, source=source)
                    for trade in fetched
                ]
                issues.extend(source_issues(fetched, source=source, identifier=context))
                self._persistence.us_trades.upsert(trades)
                if fetched.complete:
                    record_completed_scan(
                        self._persistence,
                        "us",
                        identifier,
                        source,
                        requested,
                        gap,
                        now=now,
                    )
        return scan_result(self._query(identifier, selected, requested), issues)

    def latest(
        self,
        *,
        count: int,
        sources: Sequence[str] = ("openinsider",),
        start_date: date | None = None,
        end_date: date | None = None,
        use_cache: bool = True,
        cancelled: Callable[[], bool] = not_cancelled,
    ) -> FetchResult[InsiderTrade]:
        if count < 1:
            raise ValueError("count must be positive")
        if start_date is not None or end_date is not None:
            requested = validate_range(start_date, end_date)
            return self._latest_in_range(
                count=count,
                sources=sources,
                start_date=requested.start,
                end_date=requested.end,
                use_cache=use_cache,
                cancelled=cancelled,
            )

        selected = validate_sources(sources, self._latest_sources_available())
        refresh_sources = tuple(
            source for source in selected if source in self._latest_adapters
        )
        issues = []
        if refresh_sources:
            issues = self._refresh_latest(
                refresh_sources,
                count=count,
                use_cache=use_cache,
                cancelled=cancelled,
            )
        return scan_result(
            flag_congress_copies(
                self._persistence.us_trades.query_latest(count, sources=selected)
            ),
            issues,
        )

    def _bounded_sources_available(self) -> Mapping[str, object]:
        return {**self._adapters, SEC_EDGAR_SOURCE: object()}

    def _latest_sources_available(self) -> Mapping[str, object]:
        return {**self._latest_adapters, SEC_EDGAR_SOURCE: object()}

    def _refresh_latest(
        self,
        sources: tuple[str, ...],
        *,
        count: int,
        use_cache: bool,
        cancelled: Callable[[], bool],
    ) -> list[FetchIssue]:
        fetch_count = max(count, self._latest_overlap_count)
        mode = latest_refresh_mode(fetch_count)
        now = self._clock()
        issues: list[FetchIssue] = []
        for source in sources:
            if cancelled():
                issues.append(FetchIssue("cancelled", source, "latest"))
                break
            refreshed_at = self._persistence.refresh.get("us", "*", source, mode)
            if use_cache and is_fresh(refreshed_at, now=now, ttl=self._cache_ttl):
                continue
            if not use_cache:
                self._persistence.refresh.clear("us", "*", source)
            try:
                trades = as_fetch_result(
                    self._latest_adapters[source](
                        fetch_count,
                        use_cache,
                        None,
                        None,
                    )
                )
            except (OSError, RuntimeError, ValueError) as error:
                issues.append(FetchIssue(type(error).__name__, source, "latest"))
                continue
            if cancelled():
                issues.append(FetchIssue("cancelled", source, "latest"))
                break
            issues.extend(source_issues(trades, source=source, identifier="latest"))
            self._persistence.us_trades.upsert(
                [replace(trade, source=source) for trade in trades]
            )
            if trades.complete:
                self._persistence.refresh.set("us", "*", source, mode, now)
        return issues

    def _latest_in_range(
        self,
        *,
        count: int,
        sources: Sequence[str],
        start_date: date,
        end_date: date,
        use_cache: bool,
        cancelled: Callable[[], bool],
    ) -> FetchResult[InsiderTrade]:
        selected = validate_sources(sources, self._latest_sources_available())
        fetch_count = max(count, self._latest_overlap_count)
        refresh_sources = tuple(
            source for source in selected if source in self._latest_adapters
        )
        issues: list[FetchIssue] = []
        for source in refresh_sources:
            context = f"latest {start_date}..{end_date}"
            if cancelled():
                issues.append(FetchIssue("cancelled", source, context))
                break
            try:
                trades = as_fetch_result(
                    self._latest_adapters[source](
                        fetch_count,
                        use_cache,
                        start_date,
                        end_date,
                    )
                )
            except (OSError, RuntimeError, ValueError) as error:
                issues.append(FetchIssue(type(error).__name__, source, context))
                continue
            if cancelled():
                issues.append(FetchIssue("cancelled", source, context))
                break
            issues.extend(source_issues(trades, source=source, identifier=context))
            self._persistence.us_trades.upsert(
                [replace(trade, source=source) for trade in trades]
            )
        return scan_result(
            flag_congress_copies(
                self._persistence.us_trades.query_latest(
                    count,
                    sources=selected,
                    start_date=start_date,
                    end_date=end_date,
                )
            ),
            issues,
        )

    def _query(
        self,
        identifier: str,
        sources: tuple[str, ...],
        requested,
    ) -> list[InsiderTrade]:
        return flag_congress_copies(
            self._persistence.us_trades.query(
                identifier,
                sources=sources,
                start_date=requested.start,
                end_date=requested.end,
            )
        )
