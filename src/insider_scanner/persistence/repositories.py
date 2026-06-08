"""SQLAlchemy Core repositories for persisted trade models."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, UTC

from sqlalchemy import and_, select
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


class UsTradeRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, trades: Iterable[InsiderTrade]) -> UpsertResult:
        result = UpsertResult()
        try:
            with immediate_transaction(self._engine) as connection:
                for trade in trades:
                    key = us_canonical_key(trade)
                    existing_row = (
                        connection.execute(
                            select(us_trades).where(us_trades.c.canonical_key == key)
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing_row is None:
                        connection.execute(
                            insert(us_trades).values(us_to_values(trade))
                        )
                        result += UpsertResult(inserted=1)
                        continue
                    existing = us_from_row(existing_row)
                    existing_provenance = set(us_provenance(existing_row))
                    merged = merge_trade_pair(existing, trade)
                    provenance = existing_provenance | {trade.source}
                    if merged == existing and provenance == existing_provenance:
                        result += UpsertResult(skipped=1)
                        continue
                    values = us_to_values(merged, provenance)
                    values["updated_at"] = datetime.now(UTC)
                    connection.execute(
                        us_trades.update()
                        .where(us_trades.c.canonical_key == key)
                        .values(**values)
                    )
                    result += UpsertResult(updated=1)
        except SQLAlchemyError as error:
            raise _wrap("US trade upsert", error) from error
        return result

    def query(
        self,
        ticker: str,
        *,
        sources: Iterable[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[InsiderTrade]:
        _validate_range(start_date, end_date)
        conditions = [us_trades.c.ticker == ticker.strip().upper()]
        if start_date is not None:
            conditions.append(us_trades.c.filing_date >= start_date)
        if end_date is not None:
            conditions.append(us_trades.c.filing_date <= end_date)
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
        result = UpsertResult()
        try:
            with immediate_transaction(self._engine) as connection:
                for trade in trades:
                    key = congress_canonical_key(trade)
                    existing_row = (
                        connection.execute(
                            select(congress_trades).where(
                                congress_trades.c.canonical_key == key
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing_row is None:
                        connection.execute(
                            insert(congress_trades).values(congress_to_values(trade))
                        )
                        result += UpsertResult(inserted=1)
                        continue
                    existing = congress_from_row(existing_row)
                    merged = coalesce_congress_trades(
                        existing,
                        trade,
                    )
                    if merged == existing:
                        result += UpsertResult(skipped=1)
                        continue
                    values = congress_to_values(merged)
                    values["updated_at"] = datetime.now(UTC)
                    connection.execute(
                        congress_trades.update()
                        .where(congress_trades.c.canonical_key == key)
                        .values(**values)
                    )
                    result += UpsertResult(updated=1)
        except SQLAlchemyError as error:
            raise _wrap("Congress trade upsert", error) from error
        return result

    def query(
        self,
        official: str | None = None,
        *,
        source: str | None = None,
        chamber: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[CongressTrade]:
        _validate_range(start_date, end_date)
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
        result = UpsertResult()
        try:
            with immediate_transaction(self._engine) as connection:
                for trade in trades:
                    key = european_canonical_key(trade)
                    existing_row = (
                        connection.execute(
                            select(european_trades).where(
                                european_trades.c.canonical_key == key
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing_row is None:
                        connection.execute(
                            insert(european_trades).values(european_to_values(trade))
                        )
                        result += UpsertResult(inserted=1)
                        continue
                    existing = european_from_row(existing_row)
                    existing_provenance = set(european_provenance(existing_row))
                    merged = coalesce_european_trades(existing, trade)
                    provenance = existing_provenance | {trade.source}
                    if merged == existing and provenance == existing_provenance:
                        result += UpsertResult(skipped=1)
                        continue
                    values = european_to_values(merged, provenance)
                    values["updated_at"] = datetime.now(UTC)
                    connection.execute(
                        european_trades.update()
                        .where(european_trades.c.canonical_key == key)
                        .values(**values)
                    )
                    result += UpsertResult(updated=1)
        except SQLAlchemyError as error:
            raise _wrap("European trade upsert", error) from error
        return result

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
