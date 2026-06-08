"""DB-first European insider scan service."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import AbstractSet

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.persistence.mappings import european_canonical_key
from insider_scanner.persistence.refresh import is_fresh
from insider_scanner.services.adapters import (
    EUROPEAN_BOUNDED_COVERAGE_SOURCES,
    default_european_adapters,
    default_european_latest_adapters,
)
from insider_scanner.services.common import (
    DEFAULT_CACHE_TTL,
    LATEST_OVERLAP_COUNT,
    BoundedSourceAdapter,
    LatestSourceAdapter,
    ScanError,
    latest_refresh_mode,
    normalize_identifier,
    not_cancelled,
    validate_range,
    validate_sources,
)
from insider_scanner.services.context import PersistenceContext

COUNTRY_SOURCES = {
    "UK": ("rns",),
    "DE": ("bafin",),
    "FR": ("amf",),
    "NL": ("afm",),
    "ALL": ("rns", "bafin", "amf", "afm"),
}

LATEST_COUNTRY_SOURCES = {
    "UK": ("rns",),
    "DE": ("bafin",),
    "FR": ("amf",),
    "NL": (),
    "ALL": ("rns", "bafin", "amf"),
}


class EuropeanScanService:
    def __init__(
        self,
        persistence: PersistenceContext,
        *,
        adapters: Mapping[str, BoundedSourceAdapter[EuropeanInsiderTrade]]
        | None = None,
        latest_adapters: Mapping[str, LatestSourceAdapter[EuropeanInsiderTrade]]
        | None = None,
        clock: Callable[[], datetime] | None = None,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        latest_overlap_count: int = LATEST_OVERLAP_COUNT,
        bounded_coverage_sources: AbstractSet[str] | None = None,
    ) -> None:
        self._persistence = persistence
        self._adapters = dict(
            default_european_adapters() if adapters is None else adapters
        )
        self._latest_adapters = dict(
            default_european_latest_adapters()
            if latest_adapters is None
            else latest_adapters
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache_ttl = cache_ttl
        self._bounded_coverage_sources = frozenset(
            EUROPEAN_BOUNDED_COVERAGE_SOURCES
            if bounded_coverage_sources is None
            else bounded_coverage_sources
        )
        if latest_overlap_count < 1:
            raise ValueError("latest_overlap_count must be positive")
        self._latest_overlap_count = latest_overlap_count

    def scan(
        self,
        isin: str,
        *,
        country: str = "All",
        sources: Sequence[str] | None = None,
        start_date: date | None,
        end_date: date | None,
        use_cache: bool = True,
        cancelled: Callable[[], bool] = not_cancelled,
    ) -> list[EuropeanInsiderTrade]:
        identifier = normalize_identifier(isin, "ISIN").upper()
        if len(identifier) != 12:
            raise ValueError("ISIN must be exactly 12 characters")
        if start_date is None and end_date is None:
            requested_sources = self._select_sources(country, sources, self._adapters)
            selected = tuple(
                source
                for source in requested_sources
                if source in self._latest_adapters
                and source
                in LATEST_COUNTRY_SOURCES[
                    normalize_identifier(country, "country").upper()
                ]
            )
            if not selected:
                raise ValueError("selected sources do not support unbounded scans")
            self._refresh_latest(
                selected,
                count=self._latest_overlap_count,
                use_cache=use_cache,
                cancelled=cancelled,
            )
            return self._query_identifier(identifier, selected)
        requested = validate_range(start_date, end_date)
        selected = self._select_sources(country, sources, self._adapters)

        for source in selected:
            for gap in self._persistence.coverage.gaps(
                "eu", identifier, source, requested
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
                        f"EU source {source} failed for {gap.start}..{gap.end}"
                    ) from error
                if cancelled():
                    return self._query(identifier, selected, requested)
                trades = [
                    replace(trade, isin=identifier, source=source)
                    for trade in fetched
                    if trade.isin and trade.isin.upper() == identifier
                ]
                self._persistence.european_trades.upsert(trades)
                if source in self._bounded_coverage_sources:
                    self._persistence.coverage.add("eu", identifier, source, gap)
        return self._query(identifier, selected, requested)

    def latest(
        self,
        *,
        count: int,
        isin: str | None = None,
        country: str = "All",
        sources: Sequence[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        use_cache: bool = True,
        cancelled: Callable[[], bool] = not_cancelled,
    ) -> list[EuropeanInsiderTrade]:
        if count < 1:
            raise ValueError("count must be positive")
        if start_date is not None or end_date is not None:
            validate_range(start_date, end_date)
            if isin:
                identifier = normalize_identifier(isin, "ISIN").upper()
                if len(identifier) != 12:
                    raise ValueError("ISIN must be exactly 12 characters")
                selected = self._select_sources(country, sources, self._adapters)
                return self.scan(
                    identifier,
                    country=country,
                    sources=selected,
                    start_date=start_date,
                    end_date=end_date,
                    use_cache=use_cache,
                    cancelled=cancelled,
                )[:count]
            return self._latest_in_range(
                count=count,
                country=country,
                sources=sources,
                start_date=start_date,
                end_date=end_date,
                use_cache=use_cache,
                cancelled=cancelled,
            )

        selected = self._select_latest_sources(country, sources)
        self._refresh_latest(
            selected,
            count=count,
            use_cache=use_cache,
            cancelled=cancelled,
        )
        return self._persistence.european_trades.query_latest(count, sources=selected)

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
            refreshed_at = self._persistence.refresh.get("eu", "*", source, mode)
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
                raise ScanError(f"EU source {source} latest failed") from error
            if cancelled():
                break
            self._persistence.european_trades.upsert(
                [replace(trade, source=source) for trade in trades]
            )
            self._persistence.refresh.set("eu", "*", source, mode, now)

    def _latest_in_range(
        self,
        *,
        count: int,
        country: str,
        sources: Sequence[str] | None,
        start_date: date,
        end_date: date,
        use_cache: bool,
        cancelled: Callable[[], bool],
    ) -> list[EuropeanInsiderTrade]:
        selected = self._select_latest_sources(country, sources)
        fetch_count = max(count, self._latest_overlap_count)
        for source in selected:
            if cancelled():
                break
            try:
                if source == "amf":
                    trades = self._adapters[source](
                        None,
                        start_date,
                        end_date,
                        use_cache,
                    )
                else:
                    trades = self._latest_adapters[source](
                        fetch_count,
                        use_cache,
                        start_date,
                        end_date,
                    )
            except Exception as error:
                raise ScanError(f"EU source {source} latest failed") from error
            if cancelled():
                break
            self._persistence.european_trades.upsert(
                [replace(trade, source=source) for trade in trades]
            )
        return self._persistence.european_trades.query_latest(
            count,
            sources=selected,
            start_date=start_date,
            end_date=end_date,
        )

    def _select_sources(self, country, sources, adapters):
        normalized_country = normalize_identifier(country, "country").upper()
        if normalized_country not in COUNTRY_SOURCES:
            raise ValueError(f"unknown country: {country}")
        country_sources = COUNTRY_SOURCES[normalized_country]
        requested = (
            tuple(source for source in country_sources if source in adapters)
            if sources is None
            else sources
        )
        selected = validate_sources(requested, adapters)
        outside_country = [
            source for source in selected if source not in country_sources
        ]
        if outside_country:
            raise ValueError(
                f"source(s) do not match country: {', '.join(outside_country)}"
            )
        return selected

    def _select_latest_sources(self, country, sources):
        normalized_country = normalize_identifier(country, "country").upper()
        if normalized_country not in LATEST_COUNTRY_SOURCES:
            raise ValueError(f"unknown country: {country}")
        capable_sources = LATEST_COUNTRY_SOURCES[normalized_country]
        if not capable_sources:
            raise ValueError(f"latest scans are not supported for {normalized_country}")
        requested = capable_sources if sources is None else sources
        selected = validate_sources(requested, self._latest_adapters)
        outside_country = [
            source for source in selected if source not in capable_sources
        ]
        if outside_country:
            raise ValueError(
                "source(s) do not support latest scans for "
                f"{normalized_country}: {', '.join(outside_country)}"
            )
        return selected

    def _query(self, identifier, sources, requested):
        return self._query_identifier(
            identifier,
            sources,
            start_date=requested.start,
            end_date=requested.end,
        )

    def _query_identifier(
        self,
        identifier,
        sources,
        *,
        start_date=None,
        end_date=None,
    ):
        trades: dict[str, EuropeanInsiderTrade] = {}
        for source in sources:
            for trade in self._persistence.european_trades.query(
                identifier,
                source=source,
                start_date=start_date,
                end_date=end_date,
            ):
                trades[european_canonical_key(trade)] = trade
        return sorted(
            trades.values(),
            key=lambda trade: (
                trade.trade_date or date.min,
                trade.filing_date or date.min,
                trade.source,
            ),
            reverse=True,
        )
