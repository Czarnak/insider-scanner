"""price_history persistence: repository functions, coverage checks."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select, func, and_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.persistence.engine import immediate_transaction
from insider_scanner.persistence.schema import price_history


def _to_bar(row) -> PriceBar:
    return PriceBar(
        symbol=row.symbol,
        date=row.price_date,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        adjusted_close=row.adjusted_close,
    )


def upsert_bars(engine: Engine, bars: list[PriceBar], source: str = "") -> int:
    """Insert or update bars by (symbol, date). Returns rows written."""
    if not bars:
        return 0

    written = 0
    with immediate_transaction(engine) as connection:
        for bar in bars:
            existing_row = (
                connection.execute(
                    select(price_history).where(
                        price_history.c.symbol == bar.symbol,
                        price_history.c.price_date == bar.date,
                    )
                )
                .mappings()
                .first()
            )

            if existing_row is None:
                connection.execute(
                    insert(price_history).values(
                        symbol=bar.symbol,
                        price_date=bar.date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                        adjusted_close=bar.adjusted_close,
                        source=source,
                    )
                )
            else:
                update_values = {
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "adjusted_close": bar.adjusted_close,
                }
                if source:
                    update_values["source"] = source

                connection.execute(
                    price_history.update()
                    .where(
                        price_history.c.symbol == bar.symbol,
                        price_history.c.price_date == bar.date,
                    )
                    .values(**update_values)
                )
            written += 1
    return written


def get_bars(engine: Engine, symbol: str, start: date, end: date) -> list[PriceBar]:
    """Return stored bars for symbol in [start, end], sorted by date."""
    with engine.begin() as connection:
        rows = (
            connection.execute(
                select(price_history)
                .where(
                    and_(
                        price_history.c.symbol == symbol.upper(),
                        price_history.c.price_date >= start,
                        price_history.c.price_date <= end,
                    )
                )
                .order_by(price_history.c.price_date)
            )
            .mappings()
            .all()
        )
    return [_to_bar(r) for r in rows]


def get_coverage(engine: Engine, symbol: str) -> tuple[date, date] | None:
    """Return (min_date, max_date) of stored bars for symbol, or None."""
    with engine.begin() as connection:
        row = connection.execute(
            select(
                func.min(price_history.c.price_date),
                func.max(price_history.c.price_date),
            ).where(price_history.c.symbol == symbol.upper())
        ).first()

    if not row or row[0] is None:
        return None

    # SQLite returns string dates if using standard sqlite driver sometimes,
    # but SQLAlchemy Date type handles translation. Let's ensure it's a date object.
    min_date = row[0] if isinstance(row[0], date) else date.fromisoformat(row[0])
    max_date = row[1] if isinstance(row[1], date) else date.fromisoformat(row[1])
    return (min_date, max_date)


def find_missing_ranges(
    coverage: tuple[date, date] | None, start: date, end: date
) -> list[tuple[date, date]]:
    """Edge-extension gap detection.

    Returns the sub-ranges of [start, end] not covered by ``coverage``:
    a head gap below coverage.min and a tail gap above coverage.max.
    Interior non-trading days are intentionally not re-fetched.
    """
    if coverage is None:
        return [(start, end)]
    cov_min, cov_max = coverage
    gaps: list[tuple[date, date]] = []
    if start < cov_min:
        gaps.append((start, min(end, cov_min - timedelta(days=1))))
    if end > cov_max:
        gaps.append((max(start, cov_max + timedelta(days=1)), end))
    return gaps
