"""DB-first US insider scan service."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from insider_scanner.core.models import InsiderTrade
from insider_scanner.services.adapters import (
    default_us_adapters,
    default_us_latest_adapters,
)
from insider_scanner.services.common import (
    DEFAULT_CACHE_TTL,
    LATEST_OVERLAP_COUNT,
    BoundedSourceAdapter,
    LatestSourceAdapter,
    ScanError,
    flag_congress_copies,
    latest_refresh_mode,
    normalize_identifier,
    not_cancelled,
    validate_range,
    validate_sources,
)
from insider_scanner.services.context import PersistenceContext
from insider_scanner.services.sec_comparison import SEC_EDGAR_SOURCE
from insider_scanner.persistence.refresh import is_fresh


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
    ) -> list[InsiderTrade]:
        identifier = normalize_identifier(ticker, "ticker").upper()
        if start_date is None and end_date is None:
            selected = validate_sources(sources, self._unbounded_sources_available())
            refresh_sources = tuple(
                source for source in selected if source in self._latest_adapters
            )
            local_sources = tuple(
                source for source in selected if source == SEC_EDGAR_SOURCE
            )
            if not refresh_sources and not local_sources:
                raise ValueError("selected sources do not support unbounded scans")
            if refresh_sources:
                self._refresh_latest(
                    refresh_sources,
                    count=self._latest_overlap_count,
                    use_cache=use_cache,
                    cancelled=cancelled,
                )
            return flag_congress_copies(
                self._persistence.us_trades.query(identifier, sources=selected)
            )
        requested = validate_range(start_date, end_date)
        selected = validate_sources(sources, self._bounded_sources_available())
        adapter_sources = tuple(source for source in selected if source in self._adapters)

        for source in adapter_sources:
            for gap in self._persistence.coverage.gaps(
                "us", identifier, source, requested
            ):
                if cancelled():
                    return self._query(identifier, selected, requested)
                try:
                    fetched = self._adapters[source](
                        identifier,
                        gap.start,
                        gap.end,
                        use_cache,
                    )
                except Exception as error:
                    raise ScanError(
                        f"US source {source} failed for {gap.start}..{gap.end}"
                    ) from error
                if cancelled():
                    return self._query(identifier, selected, requested)
                trades = [
                    replace(trade, ticker=identifier, source=source)
                    for trade in fetched
                ]
                self._persistence.us_trades.upsert(trades)
                self._persistence.coverage.add("us", identifier, source, gap)
        return self._query(identifier, selected, requested)

    def latest(
        self,
        *,
        count: int,
        ticker: str | None = None,
        sources: Sequence[str] = ("openinsider",),
        start_date: date | None = None,
        end_date: date | None = None,
        use_cache: bool = True,
        cancelled: Callable[[], bool] = not_cancelled,
    ) -> list[InsiderTrade]:
        if count < 1:
            raise ValueError("count must be positive")
        if start_date is not None or end_date is not None:
            validate_range(start_date, end_date)
            if ticker:
                identifier = normalize_identifier(ticker, "ticker").upper()
                selected = validate_sources(sources, self._bounded_sources_available())
                return self.scan(
                    identifier,
                    sources=selected,
                    start_date=start_date,
                    end_date=end_date,
                    use_cache=use_cache,
                    cancelled=cancelled,
                )[:count]
            assert start_date is not None
            assert end_date is not None
            return self._latest_in_range(
                count=count,
                sources=sources,
                start_date=start_date,
                end_date=end_date,
                use_cache=use_cache,
                cancelled=cancelled,
            )

        selected = validate_sources(sources, self._latest_sources_available())
        refresh_sources = tuple(
            source for source in selected if source in self._latest_adapters
        )
        if refresh_sources:
            self._refresh_latest(
                refresh_sources,
                count=count,
                use_cache=use_cache,
                cancelled=cancelled,
            )
        return flag_congress_copies(
            self._persistence.us_trades.query_latest(count, sources=selected)
        )

    def _unbounded_sources_available(self) -> Mapping[str, object]:
        return {**self._adapters, **self._latest_adapters, SEC_EDGAR_SOURCE: object()}

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
    ) -> None:
        fetch_count = max(count, self._latest_overlap_count)
        mode = latest_refresh_mode(fetch_count)
        now = self._clock()
        for source in sources:
            if cancelled():
                break
            refreshed_at = self._persistence.refresh.get("us", "*", source, mode)
            if is_fresh(refreshed_at, now=now, ttl=self._cache_ttl):
                continue
            try:
                trades = self._latest_adapters[source](
                    fetch_count,
                    use_cache,
                    None,
                    None,
                )
            except Exception as error:
                raise ScanError(f"US source {source} latest failed") from error
            if cancelled():
                break
            self._persistence.us_trades.upsert(
                [replace(trade, source=source) for trade in trades]
            )
            self._persistence.refresh.set("us", "*", source, mode, now)

    def _latest_in_range(
        self,
        *,
        count: int,
        sources: Sequence[str],
        start_date: date,
        end_date: date,
        use_cache: bool,
        cancelled: Callable[[], bool],
    ) -> list[InsiderTrade]:
        selected = validate_sources(sources, self._latest_sources_available())
        fetch_count = max(count, self._latest_overlap_count)
        refresh_sources = tuple(
            source for source in selected if source in self._latest_adapters
        )
        for source in refresh_sources:
            if cancelled():
                break
            try:
                trades = self._latest_adapters[source](
                    fetch_count,
                    use_cache,
                    start_date,
                    end_date,
                )
            except Exception as error:
                raise ScanError(f"US source {source} latest failed") from error
            if cancelled():
                break
            self._persistence.us_trades.upsert(
                [replace(trade, source=source) for trade in trades]
            )
        return flag_congress_copies(
            self._persistence.us_trades.query_latest(
                count,
                sources=selected,
                start_date=start_date,
                end_date=end_date,
            )
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
