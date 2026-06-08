"""DB-first congressional disclosure scan service."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from calendar import monthrange
from datetime import date

from insider_scanner.core.models import CongressTrade
from insider_scanner.services.adapters import default_congress_adapters
from insider_scanner.services.common import (
    BoundedSourceAdapter,
    ScanError,
    normalize_identifier,
    not_cancelled,
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
    ) -> list[CongressTrade]:
        normalized = normalize_identifier(official, "official")
        identifier = "*" if normalized.casefold() in {"*", "all"} else normalized
        if start_date is None and end_date is None:
            end_date = self._today()
            start_date = _subtract_months(end_date, 6)
        requested = validate_range(start_date, end_date)
        selected = validate_sources(sources, self._adapters)

        for source in selected:
            for gap in self._persistence.coverage.gaps(
                "congress", identifier, source, requested
            ):
                if cancelled():
                    return self._query(identifier, selected, requested)
                try:
                    fetched = self._adapters[source](
                        None if identifier == "*" else identifier,
                        gap.start,
                        gap.end,
                        use_cache,
                    )
                except Exception as error:
                    raise ScanError(
                        f"Congress source {source} failed for {gap.start}..{gap.end}"
                    ) from error
                if cancelled():
                    return self._query(identifier, selected, requested)
                self._persistence.congress_trades.upsert(
                    [replace(trade, source=source) for trade in fetched]
                )
                self._persistence.coverage.add("congress", identifier, source, gap)
        return self._query(identifier, selected, requested)

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


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)
