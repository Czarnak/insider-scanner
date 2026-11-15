"""Read-only unified transaction feed over the existing trade tables."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from functools import cmp_to_key
from math import isfinite
from typing import Any

from sqlalchemy import (
    Float,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError

from insider_scanner.core.trade_types import (
    BUY_TYPES,
    SELL_TYPES,
    TransactionDirection,
    classify_direction,
)
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.schema import (
    congress_trades,
    european_trades,
    refresh_state,
    us_trades,
)

DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500
MAX_SEARCH_LENGTH = 100
STALE_AFTER = timedelta(hours=24)


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


def _normalize_search(search: str) -> str:
    """Strip whitespace and validate length; return normalized value."""
    if not isinstance(search, str):
        raise ValueError("search must be text")  # noqa: TRY004 - public validation contract
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
            raise ValueError(f"markets contains invalid value: {m!r}")  # noqa: TRY004 - filter validation exposes ValueError
    for d in directions:
        if not isinstance(d, TransactionDirection):
            raise ValueError(f"directions contains invalid value: {d!r}")  # noqa: TRY004 - filter validation exposes ValueError
    if (
        transaction_date_from is not None
        and transaction_date_to is not None
        and transaction_date_from > transaction_date_to
    ):
        raise ValueError("transaction_date_from must not be after transaction_date_to")
    if any(
        value is not None and not isfinite(value) for value in (value_min, value_max)
    ):
        raise ValueError("value bounds must be finite")
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
        if not isinstance(self.sort_field, FeedSortField):
            raise ValueError("sort_field is invalid")  # noqa: TRY004 - public validation contract
        if not isinstance(self.descending, bool):
            raise ValueError("descending must be a boolean")  # noqa: TRY004 - public validation contract
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

    def to_query(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        cursor: FeedCursor | None = None,
        include_total: bool = True,
    ) -> FeedQuery:
        """Build a FeedQuery from this criteria plus pagination parameters."""
        return FeedQuery(
            **self._criteria_values(),
            limit=limit,
            offset=offset,
            cursor=cursor,
            include_total=include_total,
        )

    def _criteria_values(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(FeedCriteria)}

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
class FeedCursor:
    """Continuation position and insertion snapshot for one criteria."""

    criteria: FeedCriteria
    sort_value: str | float | date | None
    filing_date: date | None
    market: FeedMarket
    row_id: int
    snapshots: tuple[tuple[FeedMarket, int], ...]


@dataclass(frozen=True)
class FeedQuery(FeedCriteria):
    """Shared criteria with bounded page controls; flat keywords stay supported."""

    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0
    cursor: FeedCursor | None = None
    include_total: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if not isinstance(self.include_total, bool):
            raise ValueError("include_total must be a boolean")  # noqa: TRY004 - public validation contract
        if self.cursor is not None:
            if not isinstance(self.cursor, FeedCursor):
                raise ValueError("cursor must be a FeedCursor")
            if self.offset:
                raise ValueError("offset cannot be combined with a cursor")
            if self.cursor.criteria != self.criteria:
                raise ValueError("cursor criteria do not match query criteria")

    @property
    def criteria(self) -> FeedCriteria:
        return FeedCriteria(**self._criteria_values())


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
    created_at: datetime | None = None

    @property
    def direction(self) -> TransactionDirection:
        """Return the same direction used by models, filters and displays."""
        return classify_direction(self.transaction_type)


@dataclass(frozen=True)
class FeedPage:
    """One immutable page of normalized feed results."""

    records: tuple[FeedRecord, ...]
    total_count: int | None
    freshness_at: datetime | None
    is_stale: bool
    has_more: bool
    next_cursor: FeedCursor | None = None


@dataclass(frozen=True)
class FeedDetail:
    """Market-specific detail enriching a base FeedRecord."""

    record: FeedRecord
    shares_owned_after: float | None
    footnotes: str
    reference: str
    metadata: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class EntityHit:
    """One entity (company or insider) matched by a search."""

    kind: str
    label: str
    identifier: str
    person: str
    market: FeedMarket


@dataclass(frozen=True)
class EntitySearchResults:
    """Combined entity search results split by kind."""

    companies: tuple[EntityHit, ...]
    insiders: tuple[EntityHit, ...]


@dataclass(frozen=True)
class InvestigationBundle:
    """Aggregated context for investigating a single FeedRecord."""

    detail: FeedDetail | None
    by_person: tuple[FeedRecord, ...]
    by_issuer: tuple[FeedRecord, ...]


_MARKET_TABLES = {
    FeedMarket.US: us_trades,
    FeedMarket.CONGRESS: congress_trades,
    FeedMarket.EUROPE: european_trades,
}
_MARKET_ORDER = {market: index for index, market in enumerate(_MARKET_TABLES)}


def _projection(market: FeedMarket, sources: tuple[str, ...] = ()):
    from insider_scanner.persistence.effective import visible_condition

    us = select(
        us_trades.c.id.label("row_id"),
        (literal("us:") + cast(us_trades.c.id, String)).label("key"),
        literal(FeedMarket.US.value).label("market"),
        us_trades.c.trade_type.label("transaction_type"),
        us_trades.c.company.label("issuer"),
        us_trades.c.ticker.label("identifier"),
        us_trades.c.insider_name.label("person"),
        us_trades.c.insider_title.label("role"),
        us_trades.c.trade_date.label("transaction_date"),
        us_trades.c.filing_date.label("filing_date"),
        case((us_trades.c.shares_missing, None), else_=us_trades.c.shares).label(
            "quantity"
        ),
        case((us_trades.c.price_missing, None), else_=us_trades.c.price).label("price"),
        literal("").label("value_display"),
        case((us_trades.c.value_missing, None), else_=us_trades.c.value).label(
            "value_sort"
        ),
        literal("USD").label("currency"),
        us_trades.c.source.label("source"),
        us_trades.c.edgar_url.label("source_url"),
        us_trades.c.created_at.label("created_at"),
    ).where(visible_condition(us_trades, sources=sources))
    congress = select(
        congress_trades.c.id.label("row_id"),
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
    ).where(visible_condition(congress_trades))
    european = select(
        european_trades.c.id.label("row_id"),
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
    return {
        FeedMarket.US: us,
        FeedMarket.CONGRESS: congress,
        FeedMarket.EUROPE: european,
    }[market]


def _branch(market: FeedMarket, search: str = "", *, fields_: tuple[str, ...] = ()):
    """Keep each source independently indexable before applying a page limit."""
    from insider_scanner.persistence.search import search_index_condition

    statement = _projection(market)
    if search:
        condition = search_index_condition(
            _MARKET_TABLES[market], search, fields=fields_
        )
        if condition is not None:
            statement = statement.where(condition)
    return statement.subquery(f"feed_{market.name.lower()}")


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
        *[func.casefold(column).like(pattern, escape="\\") for column in searchable]
    )


def _direction_condition(feed, direction: TransactionDirection):
    """Return a SQLAlchemy condition for a single TransactionDirection."""
    lower_type = func.lower(func.trim(func.coalesce(feed.c.transaction_type, "")))
    if direction == TransactionDirection.BUY:
        return lower_type.in_(sorted(BUY_TYPES))
    if direction == TransactionDirection.SELL:
        return lower_type.in_(sorted(SELL_TYPES))
    return lower_type.not_in(sorted(BUY_TYPES | SELL_TYPES))


def _directions_condition(feed, directions: tuple[TransactionDirection, ...]):
    """Return a SQLAlchemy condition matching any of the given directions."""
    return or_(*[_direction_condition(feed, d) for d in directions])


def _filter_conditions(feed, query: FeedCriteria) -> list:
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
        FeedSortField.ISSUER: func.casefold(feed.c.issuer),
        FeedSortField.PERSON: func.casefold(feed.c.person),
        FeedSortField.VALUE: feed.c.value_sort,
        FeedSortField.MARKET: feed.c.market,
    }[field]


def _order_by(feed, criteria: FeedCriteria) -> tuple:
    expression = _sort_expression(feed, criteria.sort_field)
    primary = expression.desc() if criteria.descending else expression.asc()
    return (
        primary.nulls_last(),
        feed.c.filing_date.desc().nulls_last(),
        feed.c.row_id.asc(),
    )


def _row_sort_value(row, field: FeedSortField):
    value = row[
        {
            FeedSortField.TRANSACTION_DATE: "transaction_date",
            FeedSortField.FILING_DATE: "filing_date",
            FeedSortField.ISSUER: "issuer",
            FeedSortField.PERSON: "person",
            FeedSortField.VALUE: "value_sort",
            FeedSortField.MARKET: "market",
        }[field]
    ]
    return (
        value.casefold()
        if field in {FeedSortField.ISSUER, FeedSortField.PERSON}
        else value
    )


def _compare_values(left, right, *, descending: bool = False) -> int:
    if left is None or right is None:
        return (left is None) - (right is None)
    compared = (left > right) - (left < right)
    return -compared if descending else compared


def _compare_rows(left, right, criteria: FeedCriteria) -> int:
    for first, second, descending in (
        (
            _row_sort_value(left, criteria.sort_field),
            _row_sort_value(right, criteria.sort_field),
            criteria.descending,
        ),
        (left["filing_date"], right["filing_date"], True),
        (
            _MARKET_ORDER[FeedMarket(left["market"])],
            _MARKET_ORDER[FeedMarket(right["market"])],
            False,
        ),
        (left["row_id"], right["row_id"], False),
    ):
        result = _compare_values(first, second, descending=descending)
        if result:
            return result
    return 0


def _seek_regions(feed, market: FeedMarket, query: FeedQuery) -> list[list]:
    """Split a seek into disjoint ranges so SQLite can seek each source index.

    A single OR over nullable sort keys often becomes a full ordered index
    scan. Each range here has an equality prefix followed by one inequality,
    and returns at most one page of candidates.
    """
    cursor = query.cursor
    if cursor is None:
        return [[]]
    components = (
        (_sort_expression(feed, query.sort_field), cursor.sort_value, query.descending),
        (feed.c.filing_date, cursor.filing_date, True),
    )
    prefix = []
    regions = []
    for column, value, descending in components:
        if value is None:
            prefix.append(column.is_(None))
        else:
            regions.append([*prefix, column < value if descending else column > value])
            regions.append([*prefix, column.is_(None)])
            prefix.append(column == value)
    market_order = _MARKET_ORDER[market]
    cursor_order = _MARKET_ORDER[cursor.market]
    if market_order > cursor_order:
        regions.append(prefix)
    elif market_order == cursor_order:
        regions.append([*prefix, feed.c.row_id > cursor.row_id])
    return regions


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
        markets = query.markets or tuple(_MARKET_TABLES)
        total_count = 0 if query.include_total and query.cursor is None else None
        candidates: list[RowMapping] = []
        try:
            with self._engine.connect() as connection:
                snapshots = (
                    query.cursor.snapshots
                    if query.cursor is not None
                    else tuple(
                        (
                            market,
                            int(
                                connection.execute(
                                    select(func.max(_MARKET_TABLES[market].c.id))
                                ).scalar_one_or_none()
                                or 0
                            ),
                        )
                        for market in markets
                    )
                )
                for market, snapshot_id in snapshots:
                    feed = _branch(market, query.search)
                    conditions = [
                        *_filter_conditions(feed, query),
                        feed.c.row_id <= snapshot_id,
                    ]
                    if total_count is not None:
                        total_count += int(
                            connection.execute(
                                select(func.count())
                                .select_from(feed)
                                .where(*conditions)
                            ).scalar_one()
                        )
                    for region in _seek_regions(feed, market, query):
                        statement = (
                            select(feed)
                            .where(*conditions, *region)
                            .order_by(*_order_by(feed, query))
                            .limit(query.offset + query.limit + 1)
                        )
                        candidates.extend(
                            connection.execute(statement).mappings().all()
                        )
                domains = {
                    FeedMarket.US: "us",
                    FeedMarket.CONGRESS: "congress",
                    FeedMarket.EUROPE: "eu",
                }
                refreshes = [
                    _aware_utc(
                        connection.execute(
                            select(refresh_state.c.refreshed_at)
                            .where(refresh_state.c.domain == domains[market])
                            .order_by(refresh_state.c.refreshed_at.desc())
                            .limit(1)
                        ).scalar_one_or_none()
                    )
                    for market in markets
                ]
                freshness = max(
                    (stamp for stamp in refreshes if stamp is not None), default=None
                )
        except SQLAlchemyError as exc:
            raise PersistenceError("Failed to query unified transaction feed") from exc
        candidates.sort(
            key=cmp_to_key(lambda left, right: _compare_rows(left, right, query))
        )
        rows = candidates[query.offset : query.offset + query.limit]
        records = tuple(self._record_from_row(row) for row in rows)
        is_stale = (
            freshness is None
            or current_time is None
            or not timedelta(0) <= current_time - freshness <= STALE_AFTER
        )
        next_cursor = None
        if rows:
            last = rows[-1]
            next_cursor = FeedCursor(
                criteria=query.criteria,
                sort_value=_row_sort_value(last, query.sort_field),
                filing_date=last["filing_date"],
                market=FeedMarket(last["market"]),
                row_id=last["row_id"],
                snapshots=snapshots,
            )
        return FeedPage(
            records=records,
            total_count=total_count,
            freshness_at=freshness,
            is_stale=is_stale,
            has_more=len(candidates) > query.offset + query.limit,
            next_cursor=next_cursor,
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
            created_at=_aware_utc(row["created_at"]),
        )

    def detail(self, key: str) -> FeedDetail | None:
        """Return a FeedDetail for the given record key, or None if not found.

        Malformed keys, unknown prefixes, and non-integer ids all return None.
        """
        # Parse "<prefix>:<int-id>"
        if ":" not in key:
            return None
        prefix, _, raw_id = key.partition(":")
        # Reject any extra colons (e.g. "us:1:2")
        if ":" in raw_id:
            return None
        if not raw_id:
            return None
        try:
            row_id = int(raw_id)
        except ValueError:
            return None
        if str(row_id) != raw_id or row_id <= 0:
            return None

        prefix_to_market = {
            "us": FeedMarket.US,
            "congress": FeedMarket.CONGRESS,
            "europe": FeedMarket.EUROPE,
        }
        if prefix not in prefix_to_market:
            return None

        market = prefix_to_market[prefix]
        table = _MARKET_TABLES[market]
        extra_names = {
            FeedMarket.US: (
                "shares_owned_after_missing",
                "shares_owned_after",
                "edgar_url",
            ),
            FeedMarket.CONGRESS: ("owner", "party", "comment", "doc_id"),
            FeedMarket.EUROPE: (
                "instrument_type",
                "regulatory_body",
                "country",
                "source_url",
            ),
        }[market]
        stmt = (
            _projection(market)
            .add_columns(
                *[table.c[name].label(f"detail_{name}") for name in extra_names]
            )
            .where(table.c.id == row_id)
        )

        try:
            with self._engine.connect() as connection:
                row = connection.execute(stmt).mappings().first()
                if row is None:
                    return None
                record = self._record_from_row(row)
                extra = {name: row[f"detail_{name}"] for name in extra_names}

                if prefix == "us":
                    return FeedDetail(
                        record=record,
                        shares_owned_after=None
                        if extra["shares_owned_after_missing"]
                        else extra["shares_owned_after"],
                        footnotes="",
                        reference=extra["edgar_url"] or "",
                        metadata=(),
                    )

                if prefix == "congress":
                    meta: list[tuple[str, str]] = []
                    if extra["owner"]:
                        meta.append(("Owner", extra["owner"]))
                    if extra["party"]:
                        meta.append(("Party", extra["party"]))
                    return FeedDetail(
                        record=record,
                        shares_owned_after=None,
                        footnotes=extra["comment"] or "",
                        reference=extra["doc_id"] or "",
                        metadata=tuple(meta),
                    )

                # prefix == "europe"
                eu_meta: list[tuple[str, str]] = []
                if extra["instrument_type"]:
                    eu_meta.append(("Instrument", extra["instrument_type"]))
                if extra["regulatory_body"]:
                    eu_meta.append(("Regulator", extra["regulatory_body"]))
                if extra["country"]:
                    eu_meta.append(("Country", extra["country"]))
                return FeedDetail(
                    record=record,
                    shares_owned_after=None,
                    footnotes="",
                    reference=extra["source_url"] or "",
                    metadata=tuple(eu_meta),
                )

        except SQLAlchemyError as exc:
            raise PersistenceError("Failed to load feed detail") from exc

    def _recent(
        self,
        value: str,
        *,
        person: bool,
        markets: tuple[FeedMarket, ...],
        limit: int,
        exclude_key: str | None,
    ) -> tuple[FeedRecord, ...]:
        normalized = value.strip().casefold() if person else value.strip().upper()
        if not normalized:
            return ()
        query = FeedQuery(markets=markets, limit=limit, include_total=False)
        candidates: list[RowMapping] = []
        try:
            with self._engine.connect() as connection:
                for market in query.markets or tuple(_MARKET_TABLES):
                    feed = _branch(market)
                    column = (
                        func.casefold(feed.c.person) if person else feed.c.identifier
                    )
                    statement = select(feed).where(column == normalized)
                    if exclude_key is not None:
                        statement = statement.where(feed.c.key != exclude_key)
                    statement = statement.order_by(*_order_by(feed, query)).limit(limit)
                    candidates.extend(connection.execute(statement).mappings().all())
        except SQLAlchemyError as exc:
            raise PersistenceError("Failed to query recent entity records") from exc
        candidates.sort(
            key=cmp_to_key(lambda left, right: _compare_rows(left, right, query))
        )
        return tuple(self._record_from_row(row) for row in candidates[:limit])

    def recent_by_person(
        self,
        person: str,
        *,
        markets: tuple[FeedMarket, ...] = (),
        limit: int = 8,
        exclude_key: str | None = None,
    ) -> tuple[FeedRecord, ...]:
        """Read bounded per-market records using indexed Unicode person keys."""
        return self._recent(
            person, person=True, markets=markets, limit=limit, exclude_key=exclude_key
        )

    def recent_by_issuer(
        self,
        identifier: str,
        *,
        markets: tuple[FeedMarket, ...] = (),
        limit: int = 8,
        exclude_key: str | None = None,
    ) -> tuple[FeedRecord, ...]:
        """Read bounded per-market records by normalized ticker or ISIN."""
        return self._recent(
            identifier,
            person=False,
            markets=markets,
            limit=limit,
            exclude_key=exclude_key,
        )

    def search_entities(
        self,
        text: str,
        *,
        limit: int = 8,
    ) -> EntitySearchResults:
        """Search indexed Unicode substrings, merging bounded source results.

        One- and two-character searches retain a literal LIKE fallback because
        trigram indexes cannot represent those fragments.
        """
        normalized = _normalize_search(text).casefold()
        if not normalized:
            return EntitySearchResults((), ())
        FeedQuery(limit=limit)
        pattern = f"%{_escape_like(normalized)}%"
        company_rows: list[RowMapping] = []
        insider_rows: list[RowMapping] = []
        try:
            with self._engine.connect() as connection:
                for market in _MARKET_TABLES:
                    companies = _branch(
                        market, normalized, fields_=("issuer", "identifier")
                    )
                    company_condition = and_(
                        companies.c.identifier != "",
                        or_(
                            func.casefold(companies.c.identifier).like(
                                pattern, escape="\\"
                            ),
                            func.casefold(companies.c.issuer).like(
                                pattern, escape="\\"
                            ),
                        ),
                    )
                    company_statement = (
                        select(
                            companies.c.identifier,
                            companies.c.issuer,
                            companies.c.market,
                        )
                        .where(company_condition)
                        .distinct()
                        .order_by(
                            func.casefold(companies.c.issuer),
                            func.casefold(companies.c.identifier),
                        )
                        .limit(limit)
                    )
                    people = _branch(market, normalized, fields_=("person",))
                    insider_statement = (
                        select(people.c.person, people.c.market)
                        .where(
                            people.c.person != "",
                            func.casefold(people.c.person).like(pattern, escape="\\"),
                        )
                        .distinct()
                        .order_by(func.casefold(people.c.person))
                        .limit(limit)
                    )
                    company_rows.extend(
                        connection.execute(company_statement).mappings().all()
                    )
                    insider_rows.extend(
                        connection.execute(insider_statement).mappings().all()
                    )
        except SQLAlchemyError as exc:
            raise PersistenceError("Failed to search entities") from exc
        company_rows.sort(
            key=lambda row: (
                row["issuer"].casefold(),
                row["identifier"].casefold(),
                _MARKET_ORDER[FeedMarket(row["market"])],
            )
        )
        insider_rows.sort(
            key=lambda row: (
                row["person"].casefold(),
                _MARKET_ORDER[FeedMarket(row["market"])],
            )
        )
        companies = tuple(
            EntityHit(
                kind="company",
                label=row["issuer"] or row["identifier"],
                identifier=row["identifier"],
                person="",
                market=FeedMarket(row["market"]),
            )
            for row in company_rows[:limit]
        )
        insiders = tuple(
            EntityHit(
                kind="insider",
                label=row["person"],
                identifier="",
                person=row["person"],
                market=FeedMarket(row["market"]),
            )
            for row in insider_rows[:limit]
        )
        return EntitySearchResults(companies=companies, insiders=insiders)


def load_investigation(
    repository: FeedRepository,
    record: FeedRecord,
    *,
    limit: int = 8,
) -> InvestigationBundle:
    """Return an InvestigationBundle for the given record.

    Fetches detail, records by the same person, and records for the same
    issuer identifier — all excluding the anchor record itself.
    """
    return InvestigationBundle(
        detail=repository.detail(record.key),
        by_person=repository.recent_by_person(
            record.person,
            exclude_key=record.key,
            limit=limit,
        ),
        by_issuer=repository.recent_by_issuer(
            record.identifier,
            exclude_key=record.key,
            limit=limit,
        ),
    )
