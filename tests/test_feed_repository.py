"""Unified transaction feed repository tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta

import pytest

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedMarket,
    FeedQuery,
    FeedRepository,
    FeedSortField,
    TransactionDirection,
)
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    EuropeanTradeRepository,
    UsTradeRepository,
)


@pytest.fixture
def engine(tmp_path):
    engine = create_sqlite_engine(tmp_path / "feed.sqlite3")
    bootstrap_database(engine)
    yield engine
    engine.dispose()


def _seed_all_markets(engine) -> None:
    UsTradeRepository(engine).upsert(
        [
            InsiderTrade(
                ticker="AAPL",
                company="Apple Inc.",
                insider_name="Tim Cook",
                insider_title="CEO",
                trade_type="Sell",
                trade_date=date(2026, 6, 10),
                filing_date=date(2026, 6, 11),
                shares=100,
                price=200,
                value=20_000,
                source="secform4",
                edgar_url="https://example.test/aapl",
            )
        ]
    )
    CongressTradeRepository(engine).upsert(
        [
            CongressTrade(
                official_name="Jane Doe",
                chamber="House",
                filing_date=date(2026, 6, 13),
                doc_id="123",
                source_url="https://example.test/ptr",
                trade_date=date(2026, 6, 12),
                asset_description="Microsoft Corporation",
                ticker="MSFT",
                trade_type="Purchase",
                owner="Self",
                amount_range="$1,001 - $15,000",
                amount_low=1001,
                amount_high=15_000,
                source="house",
            )
        ]
    )
    EuropeanTradeRepository(engine).upsert(
        [
            EuropeanInsiderTrade(
                isin="GB0002875804",
                issuer_name="Example PLC",
                country="UK",
                regulatory_body="FCA",
                insider_name="Alex Director",
                position="Director",
                trade_date=date(2026, 6, 14),
                filing_date=date(2026, 6, 14),
                trade_type="Buy",
                instrument_type="Share",
                volume=50,
                price=10,
                currency="GBP",
                total_value=500,
                source="rns",
                source_url="https://example.test/rns",
            )
        ]
    )


def test_feed_query_is_immutable_and_validates_boundaries():
    query = FeedQuery(search="  apple  ", limit=200)

    assert query.search == "apple"
    with pytest.raises(FrozenInstanceError):
        query.limit = 10
    with pytest.raises(ValueError, match="100 characters"):
        FeedQuery(search="x" * 101)
    with pytest.raises(ValueError, match="limit"):
        FeedQuery(limit=0)
    with pytest.raises(ValueError, match="offset"):
        FeedQuery(offset=-1)


def test_feed_repository_unifies_markets_with_deterministic_latest_order(engine):
    _seed_all_markets(engine)

    page = FeedRepository(engine).query(FeedQuery(limit=2))

    assert page.total_count == 3
    assert page.has_more is True
    assert [record.market for record in page.records] == [
        FeedMarket.EUROPE,
        FeedMarket.CONGRESS,
    ]
    assert page.records[1].value_display == "$1,001 - $15,000"
    assert page.records[1].value_sort == 1001

    second_page = FeedRepository(engine).query(FeedQuery(limit=2, offset=2))
    assert [record.identifier for record in second_page.records] == ["AAPL"]
    assert second_page.has_more is False


@pytest.mark.parametrize(
    ("search", "expected_identifier"),
    [
        ("apple", "AAPL"),
        ("tim cook", "AAPL"),
        ("msft", "MSFT"),
        ("jane doe", "MSFT"),
        ("gb0002875804", "GB0002875804"),
        ("director", "GB0002875804"),
        ("rns", "GB0002875804"),
    ],
)
def test_feed_repository_searches_normalized_fields(
    engine, search, expected_identifier
):
    _seed_all_markets(engine)

    page = FeedRepository(engine).query(FeedQuery(search=search))

    assert page.total_count == 1
    assert page.records[0].identifier == expected_identifier


def test_feed_repository_treats_like_wildcards_as_literal_text(engine):
    _seed_all_markets(engine)

    page = FeedRepository(engine).query(FeedQuery(search="%_"))

    assert page.total_count == 0


def test_feed_repository_supports_validated_sorting(engine):
    _seed_all_markets(engine)

    page = FeedRepository(engine).query(
        FeedQuery(
            sort_field=FeedSortField.ISSUER,
            descending=False,
        )
    )

    assert [record.issuer for record in page.records] == [
        "Apple Inc.",
        "Example PLC",
        "Microsoft Corporation",
    ]


def test_feed_freshness_uses_latest_created_timestamp(engine):
    _seed_all_markets(engine)
    now = datetime.now(UTC)

    page = FeedRepository(engine).query(FeedQuery(), now=now)

    assert page.freshness_at is not None
    assert page.is_stale is False

    stale = FeedRepository(engine).query(
        FeedQuery(),
        now=page.freshness_at + timedelta(hours=25),
    )
    assert stale.is_stale is True


# ---------------------------------------------------------------------------
# New tests for Chunk A
# ---------------------------------------------------------------------------


def _seed_other_trade(engine) -> None:
    """Seed a US trade with a non-buy/sell trade_type for OTHER direction tests.

    Uses "Exercise" which is allowed by the schema CHECK constraint and does
    not match any BUY/SELL terms, so it maps to TransactionDirection.OTHER.
    """
    UsTradeRepository(engine).upsert(
        [
            InsiderTrade(
                ticker="GOOG",
                company="Alphabet Inc.",
                insider_name="Sundar Pichai",
                insider_title="CEO",
                trade_type="Exercise",
                trade_date=date(2026, 6, 9),
                filing_date=date(2026, 6, 10),
                shares=50,
                price=150,
                value=7_500,
                source="secform4",
                edgar_url="https://example.test/goog",
            )
        ]
    )


def test_feed_query_validates_filter_boundaries():
    """FeedQuery raises ValueError for all invalid filter combinations."""
    # date_from > date_to
    with pytest.raises(ValueError, match="transaction_date_from"):
        FeedQuery(
            transaction_date_from=date(2026, 6, 15),
            transaction_date_to=date(2026, 6, 10),
        )
    # value_min > value_max
    with pytest.raises(ValueError, match="value_min"):
        FeedQuery(value_min=1000.0, value_max=500.0)
    # negative value_min
    with pytest.raises(ValueError, match="negative"):
        FeedQuery(value_min=-1.0)
    # negative value_max
    with pytest.raises(ValueError, match="negative"):
        FeedQuery(value_max=-1.0)
    # invalid market type in markets
    with pytest.raises(ValueError, match="[Mm]arket"):
        FeedQuery(markets=("INVALID",))
    # invalid direction type in directions
    with pytest.raises(ValueError, match="[Dd]irection"):
        FeedQuery(directions=("INVALID",))


def test_feed_repository_filters_by_market(engine):
    """markets filter restricts results to matching market(s)."""
    _seed_all_markets(engine)

    # Single market
    page = FeedRepository(engine).query(FeedQuery(markets=(FeedMarket.US,)))
    assert page.total_count == 1
    assert page.records[0].market == FeedMarket.US
    assert page.records[0].identifier == "AAPL"

    # Two markets
    page2 = FeedRepository(engine).query(
        FeedQuery(markets=(FeedMarket.US, FeedMarket.CONGRESS))
    )
    assert page2.total_count == 2
    markets_returned = {r.market for r in page2.records}
    assert markets_returned == {FeedMarket.US, FeedMarket.CONGRESS}


def test_feed_repository_filters_by_direction(engine):
    """directions filter uses substring matching on transaction_type."""
    _seed_all_markets(engine)
    _seed_other_trade(engine)

    # BUY → Europe (Buy) + Congress (Purchase)
    buy_page = FeedRepository(engine).query(
        FeedQuery(directions=(TransactionDirection.BUY,))
    )
    assert buy_page.total_count == 2
    buy_markets = {r.market for r in buy_page.records}
    assert FeedMarket.EUROPE in buy_markets
    assert FeedMarket.CONGRESS in buy_markets

    # SELL → US (Sell)
    sell_page = FeedRepository(engine).query(
        FeedQuery(directions=(TransactionDirection.SELL,))
    )
    assert sell_page.total_count == 1
    assert sell_page.records[0].market == FeedMarket.US

    # OTHER → GOOG (Grant)
    other_page = FeedRepository(engine).query(
        FeedQuery(directions=(TransactionDirection.OTHER,))
    )
    assert other_page.total_count == 1
    assert other_page.records[0].identifier == "GOOG"


def test_feed_repository_filters_by_date_range(engine):
    """transaction_date_from/to filter with inclusive boundaries."""
    _seed_all_markets(engine)

    # From 2026-06-12 → Congress (2026-06-12) + Europe (2026-06-14)
    page_from = FeedRepository(engine).query(
        FeedQuery(transaction_date_from=date(2026, 6, 12))
    )
    assert page_from.total_count == 2
    markets_from = {r.market for r in page_from.records}
    assert markets_from == {FeedMarket.CONGRESS, FeedMarket.EUROPE}

    # From 2026-06-12 to 2026-06-12 → Congress only
    page_range = FeedRepository(engine).query(
        FeedQuery(
            transaction_date_from=date(2026, 6, 12),
            transaction_date_to=date(2026, 6, 12),
        )
    )
    assert page_range.total_count == 1
    assert page_range.records[0].market == FeedMarket.CONGRESS


def test_feed_repository_filters_by_value_range(engine):
    """value_min/value_max filter on value_sort column."""
    _seed_all_markets(engine)

    # value_min=1000 → AAPL (20000) + Congress (1001)
    page_min = FeedRepository(engine).query(FeedQuery(value_min=1000.0))
    assert page_min.total_count == 2
    identifiers_min = {r.identifier for r in page_min.records}
    assert identifiers_min == {"AAPL", "MSFT"}

    # value_max=600 → Europe (500)
    page_max = FeedRepository(engine).query(FeedQuery(value_max=600.0))
    assert page_max.total_count == 1
    assert page_max.records[0].market == FeedMarket.EUROPE


def test_feed_repository_combines_filters_with_search(engine):
    """Combined market filter + search returns narrowed results."""
    _seed_all_markets(engine)

    # market US + search "apple" → AAPL
    page_match = FeedRepository(engine).query(
        FeedQuery(markets=(FeedMarket.US,), search="apple")
    )
    assert page_match.total_count == 1
    assert page_match.records[0].identifier == "AAPL"

    # market US + search "msft" → 0 (mismatched combo)
    page_miss = FeedRepository(engine).query(
        FeedQuery(markets=(FeedMarket.US,), search="msft")
    )
    assert page_miss.total_count == 0


def test_feed_criteria_to_query_roundtrips_fields():
    """FeedCriteria.to_query builds a FeedQuery with all fields plus pagination."""
    criteria = FeedCriteria(
        search="apple",
        sort_field=FeedSortField.VALUE,
        descending=False,
        markets=(FeedMarket.US, FeedMarket.CONGRESS),
        directions=(TransactionDirection.BUY,),
        transaction_date_from=date(2026, 1, 1),
        transaction_date_to=date(2026, 12, 31),
        value_min=100.0,
        value_max=50_000.0,
    )

    query = criteria.to_query(limit=50, offset=10)

    assert isinstance(query, FeedQuery)
    assert query.search == "apple"
    assert query.sort_field == FeedSortField.VALUE
    assert query.descending is False
    assert query.markets == (FeedMarket.US, FeedMarket.CONGRESS)
    assert query.directions == (TransactionDirection.BUY,)
    assert query.transaction_date_from == date(2026, 1, 1)
    assert query.transaction_date_to == date(2026, 12, 31)
    assert query.value_min == 100.0
    assert query.value_max == 50_000.0
    assert query.limit == 50
    assert query.offset == 10


def test_feed_criteria_is_nontrivial():
    """is_nontrivial returns False for default/sort-only, True when any filter set."""
    default_criteria = FeedCriteria()
    assert default_criteria.is_nontrivial() is False

    # Sort-only change should NOT be nontrivial
    sort_only = FeedCriteria(sort_field=FeedSortField.VALUE, descending=False)
    assert sort_only.is_nontrivial() is False

    # Any single filter makes it nontrivial
    assert FeedCriteria(search="hello").is_nontrivial() is True
    assert FeedCriteria(markets=(FeedMarket.US,)).is_nontrivial() is True
    assert FeedCriteria(directions=(TransactionDirection.BUY,)).is_nontrivial() is True
    assert FeedCriteria(transaction_date_from=date(2026, 1, 1)).is_nontrivial() is True
    assert FeedCriteria(transaction_date_to=date(2026, 12, 31)).is_nontrivial() is True
    assert FeedCriteria(value_min=100.0).is_nontrivial() is True
    assert FeedCriteria(value_max=1000.0).is_nontrivial() is True
