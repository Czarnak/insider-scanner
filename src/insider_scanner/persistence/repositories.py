"""SQLAlchemy Core repositories for persisted trade models."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, UTC
from typing import Any

from sqlalchemy import Table, and_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.merger import merge_trade_pair
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.engine import immediate_transaction
from insider_scanner.persistence.mappings import (
    coalesce_congress_trades,
    coalesce_european_trades,
    congress_from_row,
    congress_canonical_key,
    congress_to_values,
    european_canonical_key,
    european_from_row,
    european_provenance,
    european_to_values,
    us_canonical_key,
    us_from_row,
    us_provenance,
    us_to_values,
)
from insider_scanner.persistence.schema import (
    congress_trades,
    european_trades,
    us_trades,
)


def _wrap(operation: str, error: SQLAlchemyError) -> PersistenceError:
    return PersistenceError(f"{operation} failed")


def _validate_range(start_date: date | None, end_date: date | None) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")


@dataclass(frozen=True)
class UpsertResult:
    """Counts describing the effect of one repository upsert."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    def __add__(self, other: UpsertResult) -> UpsertResult:
        if not isinstance(other, UpsertResult):
            return NotImplemented
        return UpsertResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            skipped=self.skipped + other.skipped,
        )


# SQLite's bind-variable ceiling is large (>30k on modern builds); chunk the
# canonical-key prefetch well under it so a single oversized batch can never
# exceed the limit.
_IN_CLAUSE_LIMIT = 500


def _chunked(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _batch_upsert(
    engine: Engine,
    table: Table,
    trades: Iterable[Any],
    *,
    operation: str,
    canonical_key: Callable[[Any], str],
    from_row: Callable[[Any], Any],
    merge: Callable[[Any, Any], Any],
    to_values: Callable[[Any, set[str] | None], dict[str, Any]],
    provenance_of: Callable[[Any], frozenset[str]] | None,
) -> UpsertResult:
    """Idempotent batch upsert with one bulk dedup lookup and batched writes.

    Behaviorally identical to the previous per-row SELECT-then-write loop:
    existing rows are pre-loaded with a single ``canonical_key IN (...)`` query,
    an in-memory overlay replays the same progressive merge so intra-batch
    duplicates fold exactly as before, and writes are flushed as one
    ``executemany`` insert plus grouped updates inside a single immediate
    transaction. ``provenance_of`` is ``None`` for sources without cross-source
    provenance (Congress); otherwise the merged provenance set is unioned with
    the incoming trade's source.
    """
    trade_list = list(trades)
    if not trade_list:
        return UpsertResult()

    keys = [canonical_key(trade) for trade in trade_list]
    provenance_aware = provenance_of is not None
    try:
        with immediate_transaction(engine) as connection:
            # One bulk lookup instead of one SELECT per row (eliminates N+1).
            overlay: dict[str, dict[str, Any]] = {}
            for chunk in _chunked(list(dict.fromkeys(keys)), _IN_CLAUSE_LIMIT):
                existing_rows = connection.execute(
                    select(table).where(table.c.canonical_key.in_(chunk))
                ).mappings()
                for row in existing_rows:
                    overlay[row["canonical_key"]] = dict(row)

            disposition: dict[str, str] = {}
            pending: dict[str, dict[str, Any]] = {}
            for trade, key in zip(trade_list, keys):
                current = overlay.get(key)
                if current is None:
                    values = to_values(trade, None)
                    overlay[key] = values
                    pending[key] = values
                    disposition[key] = "insert"
                    continue

                existing = from_row(current)
                merged = merge(existing, trade)
                provenance: set[str] | None = None
                if provenance_aware:
                    assert provenance_of is not None
                    existing_provenance = set(provenance_of(current))
                    provenance = existing_provenance | {trade.source}
                    unchanged = merged == existing and provenance == existing_provenance
                else:
                    unchanged = merged == existing
                if unchanged:
                    disposition.setdefault(key, "skip")
                    continue

                values = to_values(merged, provenance)
                overlay[key] = values
                pending[key] = values
                if disposition.get(key) != "insert":
                    disposition[key] = "update"

            insert_rows = [
                pending[key] for key, kind in disposition.items() if kind == "insert"
            ]
            if insert_rows:
                connection.execute(insert(table), insert_rows)

            now = datetime.now(UTC)
            for key, kind in disposition.items():
                if kind != "update":
                    continue
                connection.execute(
                    table.update()
                    .where(table.c.canonical_key == key)
                    .values(**{**pending[key], "updated_at": now})
                )

            return UpsertResult(
                inserted=sum(1 for kind in disposition.values() if kind == "insert"),
                updated=sum(1 for kind in disposition.values() if kind == "update"),
                skipped=sum(1 for kind in disposition.values() if kind == "skip"),
            )
    except SQLAlchemyError as error:
        raise _wrap(operation, error) from error


class UsTradeRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, trades: Iterable[InsiderTrade]) -> UpsertResult:
        return _batch_upsert(
            self._engine,
            us_trades,
            trades,
            operation="US trade upsert",
            canonical_key=us_canonical_key,
            from_row=us_from_row,
            merge=merge_trade_pair,
            to_values=lambda trade, provenance: us_to_values(trade, provenance),
            provenance_of=us_provenance,
        )

    def query(
        self,
        ticker: str,
        *,
        sources: Iterable[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_start_date: date | None = None,
        trade_end_date: date | None = None,
    ) -> list[InsiderTrade]:
        _validate_range(start_date, end_date)
        _validate_range(trade_start_date, trade_end_date)
        conditions = [us_trades.c.ticker == ticker.strip().upper()]
        if start_date is not None:
            conditions.append(us_trades.c.filing_date >= start_date)
        if end_date is not None:
            conditions.append(us_trades.c.filing_date <= end_date)
        if trade_start_date is not None:
            conditions.append(us_trades.c.trade_date >= trade_start_date)
        if trade_end_date is not None:
            conditions.append(us_trades.c.trade_date <= trade_end_date)
        try:
            with self._engine.begin() as connection:
                rows = connection.execute(
                    select(us_trades)
                    .where(and_(*conditions))
                    .order_by(
                        us_trades.c.filing_date.desc().nulls_last(),
                        us_trades.c.trade_date.desc().nulls_last(),
                        us_trades.c.canonical_key,
                    )
                ).mappings()
                requested_sources = (
                    {sources} if isinstance(sources, str) else set(sources or ())
                )
                return [
                    us_from_row(row)
                    for row in rows
                    if not requested_sources
                    or requested_sources.intersection(us_provenance(row))
                ]
        except SQLAlchemyError as error:
            raise _wrap("US trade query", error) from error

    def query_latest(
        self,
        count: int,
        *,
        sources: Iterable[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[InsiderTrade]:
        if count < 1:
            raise ValueError("count must be positive")
        _validate_range(start_date, end_date)
        conditions = []
        if start_date is not None:
            conditions.append(us_trades.c.filing_date >= start_date)
        if end_date is not None:
            conditions.append(us_trades.c.filing_date <= end_date)
        try:
            with self._engine.begin() as connection:
                statement = select(us_trades)
                if conditions:
                    statement = statement.where(and_(*conditions))
                rows = connection.execute(
                    statement.order_by(
                        us_trades.c.filing_date.desc().nulls_last(),
                        us_trades.c.trade_date.desc().nulls_last(),
                        us_trades.c.canonical_key,
                    )
                ).mappings()
                requested_sources = (
                    {sources} if isinstance(sources, str) else set(sources or ())
                )
                result = [
                    us_from_row(row)
                    for row in rows
                    if not requested_sources
                    or requested_sources.intersection(us_provenance(row))
                ]
                return result[:count]
        except SQLAlchemyError as error:
            raise _wrap("latest US trade query", error) from error


class CongressTradeRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, trades: Iterable[CongressTrade]) -> UpsertResult:
        return _batch_upsert(
            self._engine,
            congress_trades,
            trades,
            operation="Congress trade upsert",
            canonical_key=congress_canonical_key,
            from_row=congress_from_row,
            merge=coalesce_congress_trades,
            to_values=lambda trade, _provenance: congress_to_values(trade),
            provenance_of=None,
        )

    def query(
        self,
        official: str | None = None,
        *,
        source: str | None = None,
        chamber: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_start_date: date | None = None,
        trade_end_date: date | None = None,
    ) -> list[CongressTrade]:
        _validate_range(start_date, end_date)
        _validate_range(trade_start_date, trade_end_date)
        conditions = []
        if official and official.casefold() != "all":
            conditions.append(congress_trades.c.official_name == official)
        if source is not None:
            conditions.append(congress_trades.c.source == source)
        if chamber is not None:
            conditions.append(congress_trades.c.chamber == chamber)
        if start_date is not None:
            conditions.append(congress_trades.c.filing_date >= start_date)
        if end_date is not None:
            conditions.append(congress_trades.c.filing_date <= end_date)
        if trade_start_date is not None:
            conditions.append(congress_trades.c.trade_date >= trade_start_date)
        if trade_end_date is not None:
            conditions.append(congress_trades.c.trade_date <= trade_end_date)
        try:
            with self._engine.begin() as connection:
                statement = select(congress_trades)
                if conditions:
                    statement = statement.where(and_(*conditions))
                rows = connection.execute(
                    statement.order_by(
                        congress_trades.c.filing_date.desc().nulls_last(),
                        congress_trades.c.trade_date.desc().nulls_last(),
                        congress_trades.c.canonical_key,
                    )
                ).mappings()
                return [congress_from_row(row) for row in rows]
        except SQLAlchemyError as error:
            raise _wrap("Congress trade query", error) from error


class EuropeanTradeRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, trades: Iterable[EuropeanInsiderTrade]) -> UpsertResult:
        return _batch_upsert(
            self._engine,
            european_trades,
            trades,
            operation="European trade upsert",
            canonical_key=european_canonical_key,
            from_row=european_from_row,
            merge=coalesce_european_trades,
            to_values=lambda trade, provenance: european_to_values(trade, provenance),
            provenance_of=european_provenance,
        )

    def query(
        self,
        isin: str,
        *,
        country: str | None = None,
        source: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[EuropeanInsiderTrade]:
        _validate_range(start_date, end_date)
        conditions = [european_trades.c.isin == isin.strip().upper()]
        if country is not None:
            conditions.append(european_trades.c.country == country)
        if start_date is not None:
            conditions.append(european_trades.c.trade_date >= start_date)
        if end_date is not None:
            conditions.append(european_trades.c.trade_date <= end_date)
        try:
            with self._engine.begin() as connection:
                rows = connection.execute(
                    select(european_trades)
                    .where(and_(*conditions))
                    .order_by(
                        european_trades.c.trade_date.desc().nulls_last(),
                        european_trades.c.filing_date.desc().nulls_last(),
                        european_trades.c.canonical_key,
                    )
                ).mappings()
                return [
                    european_from_row(row)
                    for row in rows
                    if source is None or source in european_provenance(row)
                ]
        except SQLAlchemyError as error:
            raise _wrap("European trade query", error) from error

    def query_latest(
        self,
        count: int,
        *,
        sources: Iterable[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[EuropeanInsiderTrade]:
        if count < 1:
            raise ValueError("count must be positive")
        _validate_range(start_date, end_date)
        conditions = []
        requested_sources = (
            {sources} if isinstance(sources, str) else set(sources or ())
        )
        if start_date is not None:
            conditions.append(european_trades.c.trade_date >= start_date)
        if end_date is not None:
            conditions.append(european_trades.c.trade_date <= end_date)
        try:
            with self._engine.begin() as connection:
                statement = select(european_trades)
                if conditions:
                    statement = statement.where(and_(*conditions))
                rows = connection.execute(
                    statement.order_by(
                        european_trades.c.trade_date.desc().nulls_last(),
                        european_trades.c.filing_date.desc().nulls_last(),
                        european_trades.c.canonical_key,
                    )
                ).mappings()
                result = [
                    european_from_row(row)
                    for row in rows
                    if not requested_sources
                    or requested_sources.intersection(european_provenance(row))
                ]
                return result[:count]
        except SQLAlchemyError as error:
            raise _wrap("latest European trade query", error) from error
