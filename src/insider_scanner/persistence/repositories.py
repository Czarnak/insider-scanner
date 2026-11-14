"""SQLAlchemy Core repositories for persisted trade models."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from itertools import islice
from math import isnan
from typing import Any

from sqlalchemy import String, Table, and_, bindparam, func, or_, select, tuple_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError, StatementError

from insider_scanner.core.congress_identity import MemberIdentity, name_matches
from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.merger import merge_trade_pair
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence.engine import immediate_transaction
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.mappings import (
    coalesce_congress_trades,
    coalesce_european_trades,
    congress_canonical_key,
    congress_from_row,
    congress_to_values,
    european_from_row,
    european_provenance,
    european_to_values,
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
    cause = getattr(error, "orig", error)
    message = f"{type(cause).__name__}: {cause}"
    retryable = getattr(cause, "sqlite_errorcode", 0) & 255 in {5, 6}
    return PersistenceError(f"{operation} failed: {message}", retryable=retryable)


def _validate_range(start_date: date | None, end_date: date | None) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")


@dataclass(frozen=True)
class UpsertRowError:
    """A rejected input row; ``index`` refers to the original zero-based input."""

    index: int
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class UpsertResult:
    """Committed row outcomes, including isolated invalid input rows."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: tuple[UpsertRowError, ...] = ()

    @property
    def failed(self) -> int:
        return len(self.errors)

    def __add__(self, other: UpsertResult) -> UpsertResult:
        if not isinstance(other, UpsertResult):
            return NotImplemented
        return UpsertResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            skipped=self.skipped + other.skipped,
            errors=self.errors + other.errors,
        )


# Stay below SQLite's historical 999-variable limit, including Congress doc pairs.
UPSERT_BATCH_SIZE = 300


@dataclass(frozen=True)
class _BatchPolicy:
    table: Table
    to_values: Callable[..., dict]
    from_row: Callable[..., Any]
    merge: Callable[..., Any]
    provenance: Callable[..., frozenset[str]] | None = None


@dataclass
class _StoredTrade:
    values: dict
    original_key: str | None


def _row_error(error: Exception) -> bool:
    if isinstance(error, (IntegrityError, DataError)):
        return True
    cause = error.orig if isinstance(error, StatementError) else error
    return isinstance(cause, (TypeError, ValueError, AttributeError, OverflowError))


def _validate_values(
    policy: _BatchPolicy, values: dict, connection: Connection
) -> None:
    """Reject invalid inputs before a later same-key row can conceal them."""
    for name, value in values.items():
        column = policy.table.c[name]
        if value is None and not column.nullable:
            raise ValueError(f"NOT NULL constraint failed: {policy.table.name}.{name}")
        if (
            value is not None
            and isinstance(column.type, String)
            and not isinstance(value, str)
        ):
            raise TypeError(f"Invalid text {policy.table.name}.{name}")
        processor = column.type.bind_processor(connection.dialect)
        if processor is not None:
            processor(value)
    if policy.table is us_trades:
        choices = {"trade_type": {"Buy", "Sell", "Exercise", "Other"}}
        nonnegative = ("shares", "price", "value")
    elif policy.table is congress_trades:
        choices = {
            "trade_type": {"Purchase", "Sale", "Exchange", "Other"},
            "chamber": {"", "House", "Senate"},
            "source": {"", "house", "senate"},
        }
        nonnegative = ("amount_low", "amount_high")
        if values["amount_high"] < values["amount_low"]:
            raise ValueError("CHECK constraint failed: ck_congress_trades_amount_range")
    else:
        choices = {
            "trade_type": {"Buy", "Sell", "Other"},
            "country": {"", "UK", "DE", "FR", "NL"},
        }
        nonnegative = ("volume", "price", "total_value")
    for name, allowed in choices.items():
        if values[name] not in allowed:
            raise ValueError(f"Invalid {policy.table.name}.{name}")
    for name in nonnegative:
        value = values[name]
        if value is not None and (value < 0 or isnan(value)):
            raise ValueError(f"Invalid nonnegative {policy.table.name}.{name}")


def _write_batch(
    connection: Connection,
    policy: _BatchPolicy,
    indexed_trades: list[tuple[int, Any]],
) -> UpsertResult:
    """Plan merges in input order, then write each changed stored row once."""
    prepared = [(trade, policy.to_values(trade)) for _, trade in indexed_trades]
    for _, values in prepared:
        _validate_values(policy, values, connection)
    keys = [values["canonical_key"] for _, values in prepared]
    condition = policy.table.c.canonical_key.in_(keys)
    if policy.table is congress_trades:
        documents = {
            (trade.doc_id.strip(), trade.source)
            for trade, _ in prepared
            if trade.doc_id.strip()
        }
        if documents:
            # Load native and legacy observations together so promotion and old
            # exports keep their sequential semantics without per-row queries.
            condition = or_(
                condition,
                tuple_(
                    func.trim(congress_trades.c.doc_id), congress_trades.c.source
                ).in_(documents),
            )
    rows = connection.execute(select(policy.table).where(condition)).mappings()
    stored = {
        row["canonical_key"]: _StoredTrade(dict(row), row["canonical_key"])
        for row in rows
    }
    changed: dict[int, _StoredTrade] = {}
    result = UpsertResult()
    for trade, incoming_values in prepared:
        key = incoming_values["canonical_key"]
        current = stored.get(key)
        if policy.table is congress_trades and current is None:
            if trade.source_row_id and trade.doc_id:
                legacy_key = congress_canonical_key(replace(trade, source_row_id=""))
                legacy = stored.get(legacy_key)
                if legacy is not None and not legacy.values["source_row_id"]:
                    current = legacy
                    del stored[legacy_key]
            elif trade.doc_id and any(
                candidate.values["source_row_id"]
                and candidate.values["source"] == trade.source
                and candidate.values["doc_id"].strip() == trade.doc_id.strip()
                and congress_canonical_key(
                    replace(policy.from_row(candidate.values), source_row_id="")
                )
                == key
                for candidate in stored.values()
            ):
                result += UpsertResult(skipped=1)
                continue
        if current is None:
            incoming_values["updated_at"] = datetime.now(UTC)
            current = _StoredTrade(incoming_values, None)
            stored[key] = current
            changed[id(current)] = current
            result += UpsertResult(inserted=1)
            continue
        existing = policy.from_row(current.values)
        merged = policy.merge(existing, trade)
        if policy.provenance is not None:
            old_sources = set(policy.provenance(current.values))
            sources = old_sources | {trade.source}
            unchanged = merged == existing and sources == old_sources
        else:
            unchanged = merged == existing
        if unchanged:
            result += UpsertResult(skipped=1)
            continue
        values = (
            policy.to_values(merged, sources)
            if policy.provenance is not None
            else policy.to_values(merged)
        )
        values["updated_at"] = datetime.now(UTC)
        current.values = values
        stored[key] = current
        changed[id(current)] = current
        result += UpsertResult(updated=1)
    inserts = [
        record.values for record in changed.values() if record.original_key is None
    ]
    updates = [
        {**record.values, "_lookup_key": record.original_key}
        for record in changed.values()
        if record.original_key is not None
    ]
    if inserts:
        connection.execute(insert(policy.table), inserts)
    if updates:
        statement = (
            policy.table.update()
            .where(policy.table.c.canonical_key == bindparam("_lookup_key"))
            .values(
                {name: bindparam(name) for name in updates[0] if name != "_lookup_key"}
            )
        )
        connection.execute(statement, updates)
    return result


def _upsert_batches(
    engine: Engine, trades: Iterable[Any], policy: _BatchPolicy, operation: str
) -> UpsertResult:
    """Use one serialized transaction; only invalid rows use slow isolation.

    A failed executemany can have written a prefix. Roll back its savepoint
    before replaying individual rows, so counters and merge state stay accurate.
    Operational failures escape and roll back the entire outer transaction.
    """
    inputs = enumerate(trades)
    result = UpsertResult()
    try:
        with immediate_transaction(engine) as connection:
            while batch := list(islice(inputs, UPSERT_BATCH_SIZE)):
                try:
                    with connection.begin_nested():
                        batch_result = _write_batch(connection, policy, batch)
                except (
                    SQLAlchemyError,
                    TypeError,
                    ValueError,
                    AttributeError,
                    OverflowError,
                ) as error:
                    if not _row_error(error):
                        raise
                    batch_result = UpsertResult()
                    for item in batch:
                        try:
                            with connection.begin_nested():
                                row_result = _write_batch(connection, policy, [item])
                        except (
                            SQLAlchemyError,
                            TypeError,
                            ValueError,
                            AttributeError,
                            OverflowError,
                        ) as row_error:
                            if not _row_error(row_error):
                                raise
                            cause = getattr(row_error, "orig", row_error)
                            row_result = UpsertResult(
                                errors=(
                                    UpsertRowError(
                                        index=item[0],
                                        message=f"{type(cause).__name__}: {cause}",
                                    ),
                                )
                            )
                        batch_result += row_result
                result += batch_result
    except SQLAlchemyError as error:
        raise _wrap(operation, error) from error
    return result


class UsTradeRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, trades: Iterable[InsiderTrade]) -> UpsertResult:
        return _upsert_batches(
            self._engine,
            trades,
            _BatchPolicy(
                us_trades, us_to_values, us_from_row, merge_trade_pair, us_provenance
            ),
            "US trade upsert",
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
        return _upsert_batches(
            self._engine,
            trades,
            _BatchPolicy(
                congress_trades,
                congress_to_values,
                congress_from_row,
                coalesce_congress_trades,
            ),
            "Congress trade upsert",
        )

    def query(
        self,
        official: str | MemberIdentity | None = None,
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
                if official and not (
                    isinstance(official, str) and official.casefold() in {"all", "*"}
                ):
                    # Match distinct names within the requested source/date window,
                    # retaining legacy rows and their original transaction keys.
                    names = connection.execute(
                        select(congress_trades.c.official_name)
                        .where(*conditions)
                        .distinct()
                    ).scalars()
                    matching_names = [
                        name for name in names if name_matches(official, name)
                    ]
                    if not matching_names:
                        return []
                    conditions.append(
                        congress_trades.c.official_name.in_(matching_names)
                    )
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
        return _upsert_batches(
            self._engine,
            trades,
            _BatchPolicy(
                european_trades,
                european_to_values,
                european_from_row,
                coalesce_european_trades,
                european_provenance,
            ),
            "European trade upsert",
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
