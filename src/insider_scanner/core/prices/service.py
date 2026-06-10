"""Gap-fetch orchestrator: merge DB cache with on-demand source fetches."""

from __future__ import annotations

import contextlib
from datetime import date

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.core.prices.source import PriceSource, PriceSourceError
from insider_scanner.core.prices.registry import get_price_source
from insider_scanner.core.prices import repository as repo
from insider_scanner.utils.logging import get_logger

from sqlalchemy.engine import Engine

log = get_logger("prices.service")


def get_price_history(
    symbol: str,
    start: date,
    end: date,
    *,
    source: PriceSource | None = None,
    engine: Engine | None = None,
) -> list[PriceBar]:
    """Return daily bars for symbol in [start, end].

    Reads stored bars from price_history, fetches only the date ranges
    missing at the edges via ``source``, persists them, and returns the
    merged, date-sorted result. Network failures are logged and the
    cached portion is still returned (fail-soft on refresh, fail-fast
    only when there is nothing cached).
    """
    symbol = symbol.upper()
    src = source or get_price_source()

    own_engine = engine is None
    if own_engine:
        from insider_scanner.services.context import open_persistence
        ctx = open_persistence()
        e = ctx.engine
    else:
        e = engine

    try:
        coverage = repo.get_coverage(e, symbol)
        gaps = repo.find_missing_ranges(coverage, start, end)
        fetch_failed = False
        for g_start, g_end in gaps:
            try:
                fetched = src.fetch_daily(symbol, g_start, g_end)
                src_name = getattr(src, "name", "unknown")
                repo.upsert_bars(e, fetched, source=src_name)
            except Exception as exc:
                fetch_failed = True
                log.warning("Price fetch failed for %s %s..%s: %s",
                            symbol, g_start, g_end, exc)
        bars = repo.get_bars(e, symbol, start, end)
    finally:
        if own_engine and 'ctx' in locals():
            ctx.close()

    if not bars and fetch_failed:
        raise PriceSourceError("get_price_history", cause=Exception(f"No price data for {symbol} and fetch failed"))
    return bars


def validate_coverage(symbols: list[str], source: PriceSource | None = None) -> dict[str, bool]:
    """Return {symbol: has_recent_data}. Used to vet a source vs. the watchlist."""
    from datetime import timedelta

    src = source or get_price_source()
    end = date.today()
    start = end - timedelta(days=10)
    result: dict[str, bool] = {}
    for sym in symbols:
        try:
            result[sym] = bool(src.fetch_daily(sym, start, end))
        except Exception:
            result[sym] = False
    return result
