"""Provider-scoped fetched-interval cache and adjustment refresh."""

from __future__ import annotations

from datetime import UTC, date, datetime
from math import isclose

from sqlalchemy.engine import Engine

from insider_scanner.core.prices import repository as repo
from insider_scanner.core.prices.model import PriceBar
from insider_scanner.core.prices.registry import get_price_source
from insider_scanner.core.prices.source import (
    PriceSource,
    PriceSourceError,
    validate_price_request,
)
from insider_scanner.persistence.coverage import DateInterval, uncovered_gaps
from insider_scanner.utils.logging import get_logger

log = get_logger("prices.service")


def _basis_changed(old: PriceBar, new: PriceBar) -> bool:
    for before, after in (
        (old.close, new.close),
        (old.adjusted_close, new.adjusted_close),
    ):
        if before is None or after is None:
            if before != after:
                return True
        elif not isclose(before, after, rel_tol=1e-6, abs_tol=1e-8):
            return True
    return False


def _with_overlap(interval: DateInterval, cached: list[PriceBar]) -> DateInterval:
    before = [bar.date for bar in cached if bar.date < interval.start]
    after = [bar.date for bar in cached if bar.date > interval.end]
    return DateInterval(
        max(before) if before else interval.start,
        min(after) if after and not before else interval.end,
    )


def get_price_history(
    symbol: str,
    start: date,
    end: date,
    *,
    engine: Engine,
    source: PriceSource | None = None,
    now: datetime | None = None,
) -> list[PriceBar]:
    """Read bars using injected persistence and successful fetched intervals.

    Valid empty responses establish coverage. Unfinished dates expire after
    five minutes. A changed overlap triggers atomic replacement of the cached
    symbol history; a failed refresh retains the previous adjustment basis.
    """
    symbol = validate_price_request(symbol, start, end)
    checked_at = datetime.now(UTC) if now is None else now
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    checked_at = checked_at.astimezone(UTC)
    src = source or get_price_source()
    source_name = src.name
    covered = repo.fetched_intervals(engine, symbol, source_name, now=checked_at)
    gaps = uncovered_gaps(DateInterval(start, end), covered)
    bounds = repo.get_coverage(engine, symbol, source=source_name)
    cached = (
        repo.get_bars(engine, symbol, *bounds, source=source_name) if bounds else []
    )
    old_by_date = {bar.date: bar for bar in cached}
    pending: list[tuple[DateInterval, list[PriceBar]]] = []
    failure: PriceSourceError | None = None
    changed = False
    for gap in gaps:
        interval = _with_overlap(gap, cached)
        try:
            fetched = src.fetch_daily(symbol, interval.start, interval.end)
            if not fetched and not getattr(fetched, "complete", True):
                raise PriceSourceError(source_name, reason="schema")
            changed = any(
                _basis_changed(old_by_date[bar.date], bar)
                for bar in fetched
                if bar.date in old_by_date
            )
            pending.append((interval, fetched))
            if changed:
                break
        except PriceSourceError as exc:
            failure = exc
            log.warning("Price refresh failed for %s: %s", symbol, exc)
    try:
        if changed:
            full = DateInterval(
                min(
                    [
                        start,
                        *(item.start for item in covered),
                        *(bar.date for bar in cached),
                    ]
                ),
                max(
                    [
                        end,
                        *(item.end for item in covered),
                        *(bar.date for bar in cached),
                    ]
                ),
            )
            replacement = src.fetch_daily(symbol, full.start, full.end)
            if not replacement or not getattr(replacement, "complete", True):
                raise PriceSourceError(source_name, reason="schema")
            repo.store_fetch(
                engine,
                symbol,
                source_name,
                full,
                replacement,
                now=checked_at,
                complete=True,
                replace=True,
            )
        else:
            for interval, fetched in pending:
                repo.store_fetch(
                    engine,
                    symbol,
                    source_name,
                    interval,
                    fetched,
                    now=checked_at,
                    complete=getattr(fetched, "complete", True),
                )
    except PriceSourceError as exc:
        failure = exc
        log.warning("Price adjustment refresh failed for %s: %s", symbol, exc)
    except Exception as exc:  # noqa: BLE001 - retain committed cache on storage failure
        failure = PriceSourceError(source_name, cause=exc)
        log.warning("Price cache update failed for %s (%s)", symbol, type(exc).__name__)
    bars = repo.get_bars(engine, symbol, start, end, source=source_name)
    if not bars and failure is not None:
        raise failure
    return bars
