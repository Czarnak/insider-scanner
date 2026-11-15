"""price_history persistence: repository functions, coverage checks."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection, Engine

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.persistence.coverage import DateInterval, normalize_intervals
from insider_scanner.persistence.engine import immediate_transaction
from insider_scanner.persistence.schema import price_history, scan_coverage

CURRENT_BAR_TTL = timedelta(minutes=5)


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

    with immediate_transaction(engine) as connection:
        _write_bars(connection, bars, source)
    return len(bars)


def _write_bars(connection: Connection, bars: list[PriceBar], source: str) -> None:
    if not bars:
        return

    statement = insert(price_history)
    update_values = {
        name: getattr(statement.excluded, name)
        for name in ("open", "high", "low", "close", "volume", "adjusted_close")
    }
    if source:
        update_values["source"] = statement.excluded.source
    statement = statement.on_conflict_do_update(
        index_elements=[price_history.c.symbol, price_history.c.price_date],
        set_=update_values,
    )
    connection.execute(
        statement,
        [
            {
                "symbol": bar.symbol,
                "price_date": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "adjusted_close": bar.adjusted_close,
                "source": source,
            }
            for bar in bars
        ],
    )


def get_bars(
    engine: Engine, symbol: str, start: date, end: date, *, source: str | None = None
) -> list[PriceBar]:
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
                        *(
                            [price_history.c.source == source]
                            if source is not None
                            else []
                        ),
                    )
                )
                .order_by(price_history.c.price_date)
            )
            .mappings()
            .all()
        )
    return [_to_bar(r) for r in rows]


def get_coverage(
    engine: Engine, symbol: str, *, source: str | None = None
) -> tuple[date, date] | None:
    """Return (min_date, max_date) of stored bars for symbol, or None."""
    with engine.begin() as connection:
        row = connection.execute(
            select(
                func.min(price_history.c.price_date),
                func.max(price_history.c.price_date),
            ).where(
                price_history.c.symbol == symbol.upper(),
                *([price_history.c.source == source] if source is not None else []),
            )
        ).first()

    if not row or row[0] is None:
        return None

    # SQLite returns string dates if using standard sqlite driver sometimes,
    # but SQLAlchemy Date type handles translation. Let's ensure it's a date object.
    min_date = row[0] if isinstance(row[0], date) else date.fromisoformat(row[0])
    max_date = row[1] if isinstance(row[1], date) else date.fromisoformat(row[1])
    return (min_date, max_date)


def _coverage_conditions(symbol: str, source: str):
    return and_(
        scan_coverage.c.domain == "prices",
        scan_coverage.c.identifier == symbol.upper(),
        scan_coverage.c.source == source,
    )


def fetched_intervals(
    engine: Engine, symbol: str, source: str, *, now: datetime
) -> tuple[DateInterval, ...]:
    """Successful fetches; unfinished dates expire after five minutes.

    A date fetched while in progress never becomes permanent merely because
    midnight passed: its completed replacement must be fetched again.
    """
    intervals = []
    with engine.connect() as connection:
        rows = connection.execute(
            select(scan_coverage).where(_coverage_conditions(symbol, source))
        )
        for row in rows:
            checked = row.covered_at.astimezone(UTC)
            end = min(row.end_date, checked.date() - timedelta(days=1))
            if timedelta(0) <= now - checked < CURRENT_BAR_TTL:
                end = row.end_date
            if row.start_date <= end:
                intervals.append(DateInterval(row.start_date, end))
    return normalize_intervals(intervals)


def store_fetch(
    engine: Engine,
    symbol: str,
    source: str,
    interval: DateInterval,
    bars: list[PriceBar],
    *,
    now: datetime,
    complete: bool,
    replace: bool = False,
) -> None:
    """Commit bars and successful fetched intervals together.

    Legacy bars carry no inferred coverage. The date uniqueness key remains;
    overwriting another provider invalidates its coverage.
    """
    with immediate_transaction(engine) as connection:
        if replace:
            connection.execute(
                delete(price_history).where(
                    price_history.c.symbol == symbol.upper(),
                    price_history.c.source == source,
                )
            )
        if bars or replace:
            connection.execute(
                delete(scan_coverage).where(
                    scan_coverage.c.domain == "prices",
                    scan_coverage.c.identifier == symbol.upper(),
                    *([] if replace else [scan_coverage.c.source != source]),
                )
            )
        _write_bars(connection, bars, source)
        if complete:
            statement = insert(scan_coverage).values(
                domain="prices",
                identifier=symbol.upper(),
                source=source,
                start_date=interval.start,
                end_date=interval.end,
                covered_at=now,
            )
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        scan_coverage.c.domain,
                        scan_coverage.c.source,
                        scan_coverage.c.identifier,
                        scan_coverage.c.start_date,
                        scan_coverage.c.end_date,
                    ],
                    set_={"covered_at": now},
                )
            )
