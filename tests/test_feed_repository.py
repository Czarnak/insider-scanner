"""Unified transaction feed repository tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta

import pytest

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.feed import (
    FeedMarket,
    FeedQuery,
    FeedRepository,
    FeedSortField,
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
