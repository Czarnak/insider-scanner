"""DB-first congressional disclosure scan service."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult, as_fetch_result
from insider_scanner.core.models import CongressTrade
from insider_scanner.services.adapters import default_congress_adapters
from insider_scanner.services.common import (
    DEFAULT_CACHE_TTL,
    BoundedSourceAdapter,
    bounded_scan_gaps,
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


class CongressScanService:
    def __init__(
        self,
        persistence: PersistenceContext,
        *,
        adapters: Mapping[str, BoundedSourceAdapter[CongressTrade]] | None = None,
        today: Callable[[], date] | None = None,
        clock: Callable[[], datetime] | None = None,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
    ) -> None:
        self._persistence = persistence
        self._adapters = dict(
            default_congress_adapters() if adapters is None else adapters
        )
        self._clock = clock or (
            (lambda: datetime.combine(today(), time.min, tzinfo=UTC))
            if today is not None
            else (lambda: datetime.now(UTC))
        )
        self._cache_ttl = cache_ttl

    def scan(
        self,
        official: str,
        *,
        sources: Sequence[str],
        start_date: date | None,
        end_date: date | None,
        use_cache: bool = True,
        cancelled: Callable[[], bool] = not_cancelled,
    ) -> FetchResult[CongressTrade]:
        normalized = normalize_identifier(official, "official")
        identifier = "*" if normalized.casefold() in {"*", "all"} else normalized
        now = self._clock()
        if start_date is None and end_date is None:
            end_date = now.date()
            start_date = subtract_months(end_date, 6)
        requested = validate_range(start_date, end_date)
        if requested.end > now.date():
            raise ValueError("end_date must not be in the future")
        selected = validate_sources(sources, self._adapters)
        issues: list[FetchIssue] = []

        for source in selected:
            gaps = bounded_scan_gaps(
                self._persistence,
                "congress",
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
                            None if identifier == "*" else identifier,
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
                issues.extend(source_issues(fetched, source=source, identifier=context))
                self._persistence.congress_trades.upsert(
                    [replace(trade, source=source) for trade in fetched]
                )
                if fetched.complete:
                    record_completed_scan(
                        self._persistence,
                        "congress",
                        identifier,
                        source,
                        requested,
                        gap,
                        now=now,
                    )
        return scan_result(self._query(identifier, selected, requested), issues)

    def _query(self, identifier, sources, requested) -> list[CongressTrade]:
        official = None if identifier == "*" else identifier
        trades: list[CongressTrade] = []
        for source in sources:
            trades.extend(
                self._persistence.congress_trades.query(
                    official,
                    source=source,
                    start_date=requested.start,
                    end_date=requested.end,
                )
            )
        trades.sort(key=lambda trade: trade.official_name)
        trades.sort(
            key=lambda trade: (
                trade.filing_date or date.min,
                trade.trade_date or date.min,
            ),
            reverse=True,
        )
        return trades
