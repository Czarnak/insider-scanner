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

        prefix_to_market = {
            "us": FeedMarket.US,
            "congress": FeedMarket.CONGRESS,
            "europe": FeedMarket.EUROPE,
        }
        if prefix not in prefix_to_market:
            return None

        feed = _projection()
        stmt = select(feed).where(feed.c.key == key)

        try:
            with self._engine.connect() as connection:
                row = connection.execute(stmt).mappings().first()
                if row is None:
                    return None
                record = self._record_from_row(row)

                if prefix == "us":
                    extra = (
                        connection.execute(
                            select(us_trades).where(us_trades.c.id == row_id)
                        )
                        .mappings()
                        .first()
                    )
                    if extra is None:
                        return None
                    return FeedDetail(
                        record=record,
                        shares_owned_after=extra["shares_owned_after"],
                        footnotes="",
                        reference=extra["edgar_url"] or "",
                        metadata=(),
                    )

                if prefix == "congress":
                    extra = (
                        connection.execute(
                            select(congress_trades).where(
                                congress_trades.c.id == row_id
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if extra is None:
                        return None
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
                extra = (
                    connection.execute(
                        select(european_trades).where(european_trades.c.id == row_id)
                    )
                    .mappings()
                    .first()
                )
                if extra is None:
                    return None
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

    def recent_by_person(
        self,
        person: str,
        *,
        markets: tuple[FeedMarket, ...] = (),
        limit: int = 8,
        exclude_key: str | None = None,
    ) -> tuple[FeedRecord, ...]:
        """Return the most recent FeedRecords for the given person name.

        Case-insensitive match. Empty person string returns () without querying.
        """
        normalized = person.strip().casefold()
        if not normalized:
            return ()
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

        feed = _projection()
        conditions = [func.lower(func.coalesce(feed.c.person, "")) == normalized]
        if markets:
            conditions.append(feed.c.market.in_([m.value for m in markets]))
        if exclude_key is not None:
            conditions.append(feed.c.key != exclude_key)

        stmt = (
            select(feed)
            .where(and_(*conditions))
            .order_by(
                feed.c.transaction_date.is_(None),
                feed.c.transaction_date.desc(),
                feed.c.filing_date.is_(None),
                feed.c.filing_date.desc(),
                feed.c.key.asc(),
            )
            .limit(limit)
        )

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(stmt).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("Failed to query recent records by person") from exc

        return tuple(self._record_from_row(r) for r in rows)

    def recent_by_issuer(
        self,
        identifier: str,
        *,
        markets: tuple[FeedMarket, ...] = (),
        limit: int = 8,
        exclude_key: str | None = None,
    ) -> tuple[FeedRecord, ...]:
        """Return the most recent FeedRecords for the given identifier.

        Case-insensitive exact match on identifier. Empty string returns ().
        """
        normalized = identifier.strip().casefold()
        if not normalized:
            return ()
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

        feed = _projection()
        conditions = [func.lower(func.coalesce(feed.c.identifier, "")) == normalized]
        if markets:
            conditions.append(feed.c.market.in_([m.value for m in markets]))
        if exclude_key is not None:
            conditions.append(feed.c.key != exclude_key)

        stmt = (
            select(feed)
            .where(and_(*conditions))
            .order_by(
                feed.c.transaction_date.is_(None),
                feed.c.transaction_date.desc(),
                feed.c.filing_date.is_(None),
                feed.c.filing_date.desc(),
                feed.c.key.asc(),
            )
            .limit(limit)
        )

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(stmt).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("Failed to query recent records by issuer") from exc

        return tuple(self._record_from_row(r) for r in rows)

    def search_entities(
        self,
        text: str,
        *,
        limit: int = 8,
    ) -> EntitySearchResults:
        """Return companies and insiders whose names/identifiers match text.

        Empty text returns empty results without querying.
        """
        normalized = text.strip()
        if not normalized:
            return EntitySearchResults((), ())

        pattern = f"%{_escape_like(normalized.casefold())}%"

        feed = _projection()

        # Companies: distinct (identifier, issuer, market) where identifier != ""
        # and lower(identifier) LIKE pattern OR lower(issuer) LIKE pattern
        company_cond = and_(
            feed.c.identifier != "",
            or_(
                func.lower(func.coalesce(feed.c.identifier, "")).like(
                    pattern, escape="\\"
                ),
                func.lower(func.coalesce(feed.c.issuer, "")).like(pattern, escape="\\"),
            ),
        )
        company_stmt = (
            select(feed.c.identifier, feed.c.issuer, feed.c.market)
            .where(company_cond)
            .distinct()
            .order_by(
                func.lower(func.coalesce(feed.c.issuer, "")),
                func.lower(func.coalesce(feed.c.identifier, "")),
            )
            .limit(limit)
        )

        # Insiders: distinct (person, market) where person != ""
        # and lower(person) LIKE pattern
        insider_cond = and_(
            feed.c.person != "",
            func.lower(func.coalesce(feed.c.person, "")).like(pattern, escape="\\"),
        )
        insider_stmt = (
            select(feed.c.person, feed.c.market)
            .where(insider_cond)
            .distinct()
            .order_by(func.lower(func.coalesce(feed.c.person, "")))
            .limit(limit)
        )

        try:
            with self._engine.connect() as connection:
                company_rows = connection.execute(company_stmt).mappings().all()
                insider_rows = connection.execute(insider_stmt).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("Failed to search entities") from exc

        companies = tuple(
            EntityHit(
                kind="company",
                label=row["issuer"] or row["identifier"],
                identifier=row["identifier"] or "",
                person="",
                market=FeedMarket(row["market"]),
            )
            for row in company_rows
        )
        insiders = tuple(
            EntityHit(
                kind="insider",
                label=row["person"] or "",
                identifier="",
                person=row["person"] or "",
                market=FeedMarket(row["market"]),
            )
            for row in insider_rows
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
