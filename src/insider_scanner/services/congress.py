"""DB-first congressional disclosure scan service."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date

from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult, as_fetch_result
from insider_scanner.core.models import CongressTrade
from insider_scanner.services.adapters import default_congress_adapters
from insider_scanner.services.common import (
    BoundedSourceAdapter,
    normalize_identifier,
    not_cancelled,
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
    ) -> None:
        self._persistence = persistence
        self._adapters = dict(
            default_congress_adapters() if adapters is None else adapters
        )
        self._today = today or date.today

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
        if start_date is None and end_date is None:
            end_date = self._today()
            start_date = subtract_months(end_date, 6)
        requested = validate_range(start_date, end_date)
        selected = validate_sources(sources, self._adapters)
        issues: list[FetchIssue] = []

        for source in selected:
            gaps = (
                self._persistence.coverage.gaps(
                    "congress", identifier, source, requested
                )
                if use_cache
                else (requested,)
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
                    self._persistence.coverage.add("congress", identifier, source, gap)
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
