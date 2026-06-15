"""Read-only unified transaction feed over the existing trade tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Float,
    String,
    and_,
    cast,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.schema import (
    congress_trades,
    european_trades,
    us_trades,
)

DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500
MAX_SEARCH_LENGTH = 100
STALE_AFTER = timedelta(hours=24)

_BUY_TERMS: tuple[str, ...] = ("buy", "purchase")
_SELL_TERMS: tuple[str, ...] = ("sell", "sale")


class FeedMarket(StrEnum):
    """Source market represented by a normalized feed record."""

    US = "US"
    CONGRESS = "Congress"
    EUROPE = "Europe"


class FeedSortField(StrEnum):
    """Supported server-side feed sort fields."""

    TRANSACTION_DATE = "transaction_date"
    FILING_DATE = "filing_date"
    ISSUER = "issuer"
    PERSON = "person"
    VALUE = "value"
    MARKET = "market"


class TransactionDirection(StrEnum):
    """Broad direction of a transaction derived from free-text transaction_type."""

    BUY = "Buy"
    SELL = "Sell"
    OTHER = "Other"


def _normalize_search(search: str) -> str:
    """Strip whitespace and validate length; return normalized value."""
    normalized = search.strip()
    if len(normalized) > MAX_SEARCH_LENGTH:
        raise ValueError(f"search must not exceed {MAX_SEARCH_LENGTH} characters")
    return normalized


def _validate_filters(
    markets: tuple[Any, ...],
    directions: tuple[Any, ...],
    transaction_date_from: date | None,
    transaction_date_to: date | None,
    value_min: float | None,
    value_max: float | None,
) -> None:
    """Raise ValueError when any filter argument is out of range or wrong type."""
    for m in markets:
        if not isinstance(m, FeedMarket):
            raise ValueError(f"markets contains invalid value: {m!r}")
    for d in directions:
        if not isinstance(d, TransactionDirection):
            raise ValueError(f"directions contains invalid value: {d!r}")
    if (
        transaction_date_from is not None
        and transaction_date_to is not None
        and transaction_date_from > transaction_date_to
    ):
        raise ValueError("transaction_date_from must not be after transaction_date_to")
    if value_min is not None and value_min < 0:
        raise ValueError("value_min must not be negative")
    if value_max is not None and value_max < 0:
        raise ValueError("value_max must not be negative")
    if value_min is not None and value_max is not None and value_min > value_max:
        raise ValueError("value_min must not exceed value_max")


def _dedup_tuple(items: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return a deduplicated tuple preserving order."""
    seen: list[Any] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


@dataclass(frozen=True)
class FeedQuery:
    """Validated immutable query for one feed page."""

    search: str = ""
    sort_field: FeedSortField = FeedSortField.TRANSACTION_DATE
    descending: bool = True
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0
    markets: tuple[FeedMarket, ...] = ()
    directions: tuple[TransactionDirection, ...] = ()
    transaction_date_from: date | None = None
    transaction_date_to: date | None = None
    value_min: float | None = None
    value_max: float | None = None

    def __post_init__(self) -> None:
        normalized_search = _normalize_search(self.search)
        if not isinstance(self.sort_field, FeedSortField):
            raise ValueError("sort_field is invalid")
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if self.offset < 0:
            raise ValueError("offset must not be negative")
        markets = _dedup_tuple(tuple(self.markets))
        directions = _dedup_tuple(tuple(self.directions))
        _validate_filters(
            markets,
            directions,
            self.transaction_date_from,
            self.transaction_date_to,
            self.value_min,
            self.value_max,
        )
        object.__setattr__(self, "search", normalized_search)
        object.__setattr__(self, "markets", markets)
        object.__setattr__(self, "directions", directions)


@dataclass(frozen=True)
class FeedCriteria:
    """User-facing filter state without pagination.

    Use to_query() to build a paginated FeedQuery.
    """

    search: str = ""
    sort_field: FeedSortField = FeedSortField.TRANSACTION_DATE
    descending: bool = True
    markets: tuple[FeedMarket, ...] = ()
    directions: tuple[TransactionDirection, ...] = ()
    transaction_date_from: date | None = None
    transaction_date_to: date | None = None
    value_min: float | None = None
    value_max: float | None = None

    def __post_init__(self) -> None:
        normalized_search = _normalize_search(self.search)
        markets = _dedup_tuple(tuple(self.markets))
        directions = _dedup_tuple(tuple(self.directions))
        _validate_filters(
            markets,
            directions,
            self.transaction_date_from,
            self.transaction_date_to,
            self.value_min,
            self.value_max,
        )
        object.__setattr__(self, "search", normalized_search)
        object.__setattr__(self, "markets", markets)
        object.__setattr__(self, "directions", directions)

    def to_query(self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> FeedQuery:
        """Build a FeedQuery from this criteria plus pagination parameters."""
        return FeedQuery(
            search=self.search,
            sort_field=self.sort_field,
            descending=self.descending,
            limit=limit,
            offset=offset,
            markets=self.markets,
            directions=self.directions,
            transaction_date_from=self.transaction_date_from,
            transaction_date_to=self.transaction_date_to,
            value_min=self.value_min,
            value_max=self.value_max,
        )

    def is_nontrivial(self) -> bool:
        """Return True if any filter (search/markets/directions/dates/values) is set.

        Returns False when only sort/descending differ from defaults.
        """
        return bool(
            self.search
            or self.markets
            or self.directions
            or self.transaction_date_from is not None
            or self.transaction_date_to is not None
            or self.value_min is not None
            or self.value_max is not None
        )


@dataclass(frozen=True)
class FeedRecord:
    """One normalized transaction from any persisted market."""

    key: str
    market: FeedMarket
    transaction_type: str
    issuer: str
    identifier: str
    person: str
    role: str
    transaction_date: date | None
    filing_date: date | None
    quantity: float | None
    price: float | None
    value_display: str
    value_sort: float | None
    currency: str
    source: str
    source_url: str


@dataclass(frozen=True)
class FeedPage:
    """One immutable page of normalized feed results."""

    records: tuple[FeedRecord, ...]
    total_count: int
    freshness_at: datetime | None
    is_stale: bool
    has_more: bool


def _projection():
    us = select(
        (literal("us:") + cast(us_trades.c.id, String)).label("key"),
        literal(FeedMarket.US.value).label("market"),
        us_trades.c.trade_type.label("transaction_type"),
        us_trades.c.company.label("issuer"),
        us_trades.c.ticker.label("identifier"),
        us_trades.c.insider_name.label("person"),
        us_trades.c.insider_title.label("role"),
        us_trades.c.trade_date.label("transaction_date"),
        us_trades.c.filing_date.label("filing_date"),
        us_trades.c.shares.label("quantity"),
        us_trades.c.price.label("price"),
        literal("").label("value_display"),
        us_trades.c.value.label("value_sort"),
        literal("USD").label("currency"),
        us_trades.c.source.label("source"),
        us_trades.c.edgar_url.label("source_url"),
        us_trades.c.created_at.label("created_at"),
    )
    congress = select(
        (literal("congress:") + cast(congress_trades.c.id, String)).label("key"),
        literal(FeedMarket.CONGRESS.value).label("market"),
        congress_trades.c.trade_type.label("transaction_type"),
        congress_trades.c.asset_description.label("issuer"),
        congress_trades.c.ticker.label("identifier"),
        congress_trades.c.official_name.label("person"),
        congress_trades.c.chamber.label("role"),
        congress_trades.c.trade_date.label("transaction_date"),
        congress_trades.c.filing_date.label("filing_date"),
        cast(literal(None), Float).label("quantity"),
        cast(literal(None), Float).label("price"),
        congress_trades.c.amount_range.label("value_display"),
        congress_trades.c.amount_low.label("value_sort"),
        literal("USD").label("currency"),
        congress_trades.c.source.label("source"),
        congress_trades.c.source_url.label("source_url"),
        congress_trades.c.created_at.label("created_at"),
    )
    european = select(
        (literal("europe:") + cast(european_trades.c.id, String)).label("key"),
        literal(FeedMarket.EUROPE.value).label("market"),
        european_trades.c.trade_type.label("transaction_type"),
        european_trades.c.issuer_name.label("issuer"),
        european_trades.c.isin.label("identifier"),
        european_trades.c.insider_name.label("person"),
        european_trades.c.position.label("role"),
        european_trades.c.trade_date.label("transaction_date"),
        european_trades.c.filing_date.label("filing_date"),
        european_trades.c.volume.label("quantity"),
        european_trades.c.price.label("price"),
        literal("").label("value_display"),
        european_trades.c.total_value.label("value_sort"),
        european_trades.c.currency.label("currency"),
        european_trades.c.source.label("source"),
        european_trades.c.source_url.label("source_url"),
        european_trades.c.created_at.label("created_at"),
    )
    return union_all(us, congress, european).subquery("unified_feed")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_condition(feed, search: str):
    pattern = f"%{_escape_like(search.casefold())}%"
    searchable = (
        feed.c.market,
        feed.c.transaction_type,
        feed.c.issuer,
        feed.c.identifier,
        feed.c.person,
        feed.c.role,
        feed.c.source,
    )
    return or_(
        *[
            func.lower(func.coalesce(column, "")).like(pattern, escape="\\")
            for column in searchable
        ]
    )


def _direction_condition(feed, direction: TransactionDirection):
    """Return a SQLAlchemy condition for a single TransactionDirection."""
    lower_type = func.lower(func.coalesce(feed.c.transaction_type, ""))
    if direction == TransactionDirection.BUY:
        return or_(*[lower_type.like(f"%{term}%") for term in _BUY_TERMS])
    if direction == TransactionDirection.SELL:
        return or_(*[lower_type.like(f"%{term}%") for term in _SELL_TERMS])
    # OTHER: matches neither buy nor sell terms
    buy_sell_terms = _BUY_TERMS + _SELL_TERMS
    return ~or_(*[lower_type.like(f"%{term}%") for term in buy_sell_terms])


def _directions_condition(feed, directions: tuple[TransactionDirection, ...]):
    """Return a SQLAlchemy condition matching any of the given directions."""
    return or_(*[_direction_condition(feed, d) for d in directions])


def _filter_conditions(feed, query: FeedQuery) -> list:
    """Build a list of SQLAlchemy conditions from the query's filter fields.

    Range filters (transaction_date, value_sort) exclude rows with NULL values.
    """
    conditions = []
    if query.search:
        conditions.append(_search_condition(feed, query.search))
    if query.markets:
        conditions.append(feed.c.market.in_([m.value for m in query.markets]))
    if query.directions:
        conditions.append(_directions_condition(feed, query.directions))
    if query.transaction_date_from is not None:
        conditions.append(feed.c.transaction_date >= query.transaction_date_from)
    if query.transaction_date_to is not None:
        conditions.append(feed.c.transaction_date <= query.transaction_date_to)
    if query.value_min is not None:
        conditions.append(feed.c.value_sort >= query.value_min)
    if query.value_max is not None:
        conditions.append(feed.c.value_sort <= query.value_max)
    return conditions


def _sort_expression(feed, field: FeedSortField):
    return {
        FeedSortField.TRANSACTION_DATE: feed.c.transaction_date,
        FeedSortField.FILING_DATE: feed.c.filing_date,
        FeedSortField.ISSUER: func.lower(feed.c.issuer),
        FeedSortField.PERSON: func.lower(feed.c.person),
        FeedSortField.VALUE: feed.c.value_sort,
        FeedSortField.MARKET: feed.c.market,
    }[field]


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class FeedRepository:
    """Query a normalized, paged transaction timeline without copying data."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def query(
        self,
        query: FeedQuery,
        *,
        now: datetime | None = None,
    ) -> FeedPage:
        """Return a paged FeedPage for the given FeedQuery.

        Filter fields are applied via AND logic. Range filters on
        transaction_date and value_sort exclude rows where those columns are NULL.
        """
        if not isinstance(query, FeedQuery):
            raise TypeError("query must be a FeedQuery")
        current_time = _aware_utc(now or datetime.now(UTC))
        feed = _projection()
        conditions = _filter_conditions(feed, query)
        sort_expression = _sort_expression(feed, query.sort_field)
        direction = (
            sort_expression.desc() if query.descending else sort_expression.asc()
        )
        statement = select(feed)
        count_statement = select(func.count()).select_from(feed)
        if conditions:
            combined = and_(*conditions)
            statement = statement.where(combined)
            count_statement = count_statement.where(combined)
        statement = (
            statement.order_by(
                sort_expression.is_(None),
                direction,
                feed.c.filing_date.is_(None),
                feed.c.filing_date.desc(),
                feed.c.key.asc(),
            )
            .limit(query.limit)
            .offset(query.offset)
        )

        try:
            with self._engine.connect() as connection:
                total_count = int(connection.execute(count_statement).scalar_one())
                rows = connection.execute(statement).mappings().all()
                freshness = _aware_utc(
                    connection.execute(
                        select(func.max(feed.c.created_at))
                    ).scalar_one_or_none()
                )
        except SQLAlchemyError as exc:
            raise PersistenceError("Failed to query unified transaction feed") from exc

        records = tuple(self._record_from_row(row) for row in rows)
        is_stale = (
            freshness is not None
            and current_time is not None
            and current_time - freshness > STALE_AFTER
        )
        return FeedPage(
            records=records,
            total_count=total_count,
            freshness_at=freshness,
            is_stale=is_stale,
            has_more=query.offset + len(records) < total_count,
        )

    @staticmethod
    def _record_from_row(row) -> FeedRecord:
        return FeedRecord(
            key=str(row["key"]),
            market=FeedMarket(row["market"]),
            transaction_type=row["transaction_type"] or "Other",
            issuer=row["issuer"] or "",
            identifier=row["identifier"] or "",
            person=row["person"] or "",
            role=row["role"] or "",
            transaction_date=row["transaction_date"],
            filing_date=row["filing_date"],
            quantity=row["quantity"],
            price=row["price"],
            value_display=row["value_display"] or "",
            value_sort=row["value_sort"],
            currency=row["currency"] or "",
            source=row["source"] or "",
            source_url=row["source_url"] or "",
        )
