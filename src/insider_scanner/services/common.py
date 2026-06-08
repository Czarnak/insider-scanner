"""Shared scan-service contracts and validation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import date, timedelta
from typing import Protocol, TypeVar

from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.senate import flag_congress_trades
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.utils.config import DEFAULT_CACHE_TTL as CACHE_TTL_SECONDS

DEFAULT_CACHE_TTL = timedelta(seconds=CACHE_TTL_SECONDS)
# Latest endpoints refresh a recent overlapping tail to absorb late filings.
LATEST_OVERLAP_COUNT = 200

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


def normalize_identifier(value: str, label: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def validate_range(
    start_date: date | None,
    end_date: date | None,
) -> DateInterval:
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must both be present")
    if start_date is None or end_date is None:
        raise ValueError("start_date and end_date must both be present")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    return DateInterval(start_date, end_date)


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
