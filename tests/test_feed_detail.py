"""Tests for FeedRepository detail methods, entity search, and investigation bundle.

Covers: detail(), recent_by_person(), recent_by_issuer(), search_entities(),
        load_investigation(), and all new dataclasses.

TDD cycle: tests written FIRST (RED), then implementation (GREEN).
"""

from __future__ import annotations

from datetime import date

import pytest

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.feed import (
    EntityHit,
    EntitySearchResults,
    FeedDetail,
    FeedMarket,
    FeedRecord,
    FeedRepository,
    InvestigationBundle,
    load_investigation,
)
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    EuropeanTradeRepository,
    UsTradeRepository,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    db = create_sqlite_engine(tmp_path / "feed_detail.sqlite3")
    bootstrap_database(db)
    yield db
    db.dispose()


def _seed_all_markets(engine) -> None:
    """Seed one record per market with all relevant detail fields populated."""
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
                shares_owned_after=5000.0,
                source="secform4",
                edgar_url="https://edgar.test/aapl-form4",
            )
        ]
    )
    CongressTradeRepository(engine).upsert(
        [
            CongressTrade(
                official_name="Jane Doe",
                chamber="House",
                party="Democrat",
                filing_date=date(2026, 6, 13),
                doc_id="DOC-456",
                source_url="https://example.test/ptr",
                trade_date=date(2026, 6, 12),
                asset_description="Microsoft Corporation",
                ticker="MSFT",
                trade_type="Purchase",
                owner="Joint",
                amount_range="$1,001 - $15,000",
                amount_low=1001,
                amount_high=15_000,
                comment="Quarterly rebalance",
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


# ---------------------------------------------------------------------------
# FeedDetail dataclass
# ---------------------------------------------------------------------------


def test_feed_detail_is_frozen():
    """FeedDetail must be a frozen dataclass."""
    from dataclasses import FrozenInstanceError

    record = FeedRecord(
        key="us:1",
        market=FeedMarket.US,
        transaction_type="Sell",
        issuer="Apple Inc.",
        identifier="AAPL",
        person="Tim Cook",
        role="CEO",
        transaction_date=date(2026, 6, 10),
        filing_date=date(2026, 6, 11),
        quantity=100.0,
        price=200.0,
        value_display="",
        value_sort=20000.0,
        currency="USD",
        source="secform4",
        source_url="https://edgar.test/aapl-form4",
    )
    detail = FeedDetail(
        record=record,
        shares_owned_after=5000.0,
        footnotes="",
        reference="https://edgar.test/aapl-form4",
        metadata=(),
    )
    with pytest.raises(FrozenInstanceError):
        detail.footnotes = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# detail() — US market
# ---------------------------------------------------------------------------


def test_detail_us_returns_correct_record_and_extras(engine):
    """detail() for a US key returns base record, shares_owned_after, edgar reference."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.detail("us:1")

    assert result is not None
    assert isinstance(result, FeedDetail)
    assert result.record.market == FeedMarket.US
    assert result.record.identifier == "AAPL"
    assert result.record.person == "Tim Cook"
    assert result.shares_owned_after == 5000.0
    assert result.reference == "https://edgar.test/aapl-form4"
    assert result.footnotes == ""
    assert result.metadata == ()


# ---------------------------------------------------------------------------
# detail() — Congress market
# ---------------------------------------------------------------------------


def test_detail_congress_returns_correct_record_and_extras(engine):
    """detail() for a congress key returns base record, doc_id reference, comment footnote, owner/party metadata."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.detail("congress:1")

    assert result is not None
    assert result.record.market == FeedMarket.CONGRESS
    assert result.record.identifier == "MSFT"
    assert result.shares_owned_after is None
    assert result.reference == "DOC-456"
    assert result.footnotes == "Quarterly rebalance"
    # metadata must include ("Owner", "Joint") and ("Party", "Democrat")
    assert ("Owner", "Joint") in result.metadata
    assert ("Party", "Democrat") in result.metadata


# ---------------------------------------------------------------------------
# detail() — Europe market
# ---------------------------------------------------------------------------


def test_detail_europe_returns_correct_record_and_extras(engine):
    """detail() for a europe key returns base record, source_url reference, instrument/regulatory/country metadata."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.detail("europe:1")

    assert result is not None
    assert result.record.market == FeedMarket.EUROPE
    assert result.record.identifier == "GB0002875804"
    assert result.shares_owned_after is None
    assert result.reference == "https://example.test/rns"
    assert result.footnotes == ""
    assert ("Instrument", "Share") in result.metadata
    assert ("Regulator", "FCA") in result.metadata
    assert ("Country", "UK") in result.metadata


# ---------------------------------------------------------------------------
# detail() — bad inputs → None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "us:abc",  # non-int id
        "unknown:1",  # unrecognized prefix
        "nocoion",  # no colon at all
        "",  # empty string
        "us:",  # missing id
        ":1",  # missing prefix
        "us:1:2",  # extra colon
    ],
)
def test_detail_returns_none_for_malformed_keys(engine, bad_key):
    """detail() returns None for all malformed or unknown-prefix keys."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    assert repo.detail(bad_key) is None


def test_detail_returns_none_for_missing_record(engine):
    """detail() returns None when the id does not exist in the database."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    assert repo.detail("us:9999") is None
    assert repo.detail("congress:9999") is None
    assert repo.detail("europe:9999") is None


# ---------------------------------------------------------------------------
# recent_by_person()
# ---------------------------------------------------------------------------


def test_recent_by_person_returns_matching_records(engine):
    """recent_by_person() finds records case-insensitively by person name."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    results = repo.recent_by_person("tim cook")

    assert len(results) == 1
    assert results[0].person == "Tim Cook"
    assert results[0].market == FeedMarket.US


def test_recent_by_person_case_insensitive(engine):
    """recent_by_person() matches regardless of input case."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    upper = repo.recent_by_person("TIM COOK")
    mixed = repo.recent_by_person("Tim Cook")

    assert len(upper) == 1
    assert len(mixed) == 1
    assert upper[0].key == mixed[0].key


def test_recent_by_person_empty_name_returns_empty(engine):
    """recent_by_person() returns () immediately for blank person string."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    assert repo.recent_by_person("") == ()
    assert repo.recent_by_person("   ") == ()


def test_recent_by_person_with_market_filter(engine):
    """recent_by_person() respects the markets tuple filter."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    # Jane Doe is in CONGRESS; filter by US → no match
    results = repo.recent_by_person("jane doe", markets=(FeedMarket.US,))
    assert results == ()

    # Filter by CONGRESS → match
    results = repo.recent_by_person("jane doe", markets=(FeedMarket.CONGRESS,))
    assert len(results) == 1


def test_recent_by_person_exclude_key(engine):
    """recent_by_person() excludes the specified key."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    all_results = repo.recent_by_person("tim cook")
    assert len(all_results) == 1

    excluded = repo.recent_by_person("tim cook", exclude_key="us:1")
    assert excluded == ()


def test_recent_by_person_limit(engine):
    """recent_by_person() respects the limit parameter."""
    # Seed two trades for the same person
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
            ),
            InsiderTrade(
                ticker="MSFT",
                company="Microsoft Corp.",
                insider_name="Tim Cook",
                insider_title="CEO",
                trade_type="Buy",
                trade_date=date(2026, 6, 8),
                filing_date=date(2026, 6, 9),
                shares=50,
                price=300,
                value=15_000,
                source="secform4",
            ),
        ]
    )
    repo = FeedRepository(engine)

    results = repo.recent_by_person("tim cook", limit=1)
    assert len(results) == 1


def test_recent_by_person_ordering_transaction_date_desc(engine):
    """recent_by_person() orders by transaction_date descending."""
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
            ),
            InsiderTrade(
                ticker="MSFT",
                company="Microsoft Corp.",
                insider_name="Tim Cook",
                insider_title="CEO",
                trade_type="Buy",
                trade_date=date(2026, 6, 8),
                filing_date=date(2026, 6, 9),
                shares=50,
                price=300,
                value=15_000,
                source="secform4",
            ),
        ]
    )
    repo = FeedRepository(engine)

    results = repo.recent_by_person("tim cook")

    assert len(results) == 2
    # Most recent trade_date first
    assert results[0].transaction_date == date(2026, 6, 10)
    assert results[1].transaction_date == date(2026, 6, 8)


# ---------------------------------------------------------------------------
# recent_by_issuer()
# ---------------------------------------------------------------------------


def test_recent_by_issuer_returns_matching_records(engine):
    """recent_by_issuer() finds records case-insensitively by identifier."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    results = repo.recent_by_issuer("aapl")

    assert len(results) == 1
    assert results[0].identifier == "AAPL"


def test_recent_by_issuer_empty_identifier_returns_empty(engine):
    """recent_by_issuer() returns () immediately for blank identifier."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    assert repo.recent_by_issuer("") == ()
    assert repo.recent_by_issuer("   ") == ()


def test_recent_by_issuer_with_market_filter(engine):
    """recent_by_issuer() respects the markets tuple filter."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    results = repo.recent_by_issuer("MSFT", markets=(FeedMarket.EUROPE,))
    assert results == ()

    results = repo.recent_by_issuer("MSFT", markets=(FeedMarket.CONGRESS,))
    assert len(results) == 1


def test_recent_by_issuer_exclude_key(engine):
    """recent_by_issuer() excludes the specified key."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    all_results = repo.recent_by_issuer("AAPL")
    assert len(all_results) == 1

    excluded = repo.recent_by_issuer("AAPL", exclude_key="us:1")
    assert excluded == ()


def test_recent_by_issuer_limit(engine):
    """recent_by_issuer() respects the limit parameter."""
    EuropeanTradeRepository(engine).upsert(
        [
            EuropeanInsiderTrade(
                isin="GB0002875804",
                issuer_name="Example PLC",
                country="UK",
                regulatory_body="FCA",
                insider_name="Alice",
                position="Director",
                trade_date=date(2026, 6, 14),
                filing_date=date(2026, 6, 14),
                trade_type="Buy",
                volume=50,
                price=10,
                currency="GBP",
                total_value=500,
                source="rns",
                source_url="https://example.test/rns1",
            ),
            EuropeanInsiderTrade(
                isin="GB0002875804",
                issuer_name="Example PLC",
                country="UK",
                regulatory_body="FCA",
                insider_name="Bob",
                position="CFO",
                trade_date=date(2026, 6, 13),
                filing_date=date(2026, 6, 13),
                trade_type="Buy",
                volume=25,
                price=10,
                currency="GBP",
                total_value=250,
                source="rns",
                source_url="https://example.test/rns2",
            ),
        ]
    )
    repo = FeedRepository(engine)

    results = repo.recent_by_issuer("GB0002875804", limit=1)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# search_entities()
# ---------------------------------------------------------------------------


def test_search_entities_empty_text_returns_empty(engine):
    """search_entities() returns empty results for blank text."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.search_entities("")
    assert isinstance(result, EntitySearchResults)
    assert result.companies == ()
    assert result.insiders == ()

    result = repo.search_entities("   ")
    assert result.companies == ()
    assert result.insiders == ()


def test_search_entities_finds_company_by_issuer_name(engine):
    """search_entities() finds company hits by issuer name."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.search_entities("apple")

    assert len(result.companies) == 1
    hit = result.companies[0]
    assert hit.kind == "company"
    assert hit.label == "Apple Inc."
    assert hit.identifier == "AAPL"
    assert hit.market == FeedMarket.US


def test_search_entities_finds_company_by_identifier(engine):
    """search_entities() finds company hits by identifier (ticker/ISIN)."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.search_entities("msft")

    assert len(result.companies) >= 1
    identifiers = [h.identifier for h in result.companies]
    assert "MSFT" in identifiers


def test_search_entities_finds_insider_by_name(engine):
    """search_entities() finds insider hits by person name."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.search_entities("tim cook")

    assert len(result.insiders) == 1
    hit = result.insiders[0]
    assert hit.kind == "insider"
    assert hit.label == "Tim Cook"
    assert hit.person == "Tim Cook"
    assert hit.identifier == ""
    assert hit.market == FeedMarket.US


def test_search_entities_distinct_companies(engine):
    """search_entities() returns distinct (identifier, market) company combinations."""
    # Seed two trades for the same identifier/issuer to confirm dedup
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
            ),
            InsiderTrade(
                ticker="AAPL",
                company="Apple Inc.",
                insider_name="Jony Ive",
                insider_title="CDO",
                trade_type="Sell",
                trade_date=date(2026, 6, 9),
                filing_date=date(2026, 6, 10),
                shares=50,
                price=200,
                value=10_000,
                source="secform4",
            ),
        ]
    )
    repo = FeedRepository(engine)

    result = repo.search_entities("apple")

    # AAPL/Apple Inc. should appear only once as a company hit
    aapl_hits = [h for h in result.companies if h.identifier == "AAPL"]
    assert len(aapl_hits) == 1


def test_search_entities_distinct_insiders(engine):
    """search_entities() returns distinct (person, market) insider combinations."""
    # Two trades by same person
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
            ),
            InsiderTrade(
                ticker="MSFT",
                company="Microsoft Corp.",
                insider_name="Tim Cook",
                insider_title="CEO",
                trade_type="Buy",
                trade_date=date(2026, 6, 8),
                filing_date=date(2026, 6, 9),
                shares=50,
                price=300,
                value=15_000,
                source="secform4",
            ),
        ]
    )
    repo = FeedRepository(engine)

    result = repo.search_entities("tim cook")

    # Tim Cook (US market) appears only once
    cook_hits = [h for h in result.insiders if h.person == "Tim Cook"]
    assert len(cook_hits) == 1


def test_search_entities_like_wildcards_treated_as_literal(engine):
    """search_entities() treats % and _ as literal characters."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.search_entities("%_")

    assert result.companies == ()
    assert result.insiders == ()


def test_search_entities_limit(engine):
    """search_entities() caps results at the given limit."""
    # Seed 3 distinct companies all matching "inc"
    UsTradeRepository(engine).upsert(
        [
            InsiderTrade(
                ticker="AAPL",
                company="Apple Inc.",
                insider_name="Tim Cook",
                trade_type="Sell",
                trade_date=date(2026, 6, 10),
                shares=10,
                price=200,
                value=2000,
                source="secform4",
            ),
            InsiderTrade(
                ticker="GOOG",
                company="Alphabet Inc.",
                insider_name="Sundar Pichai",
                trade_type="Sell",
                trade_date=date(2026, 6, 10),
                shares=10,
                price=100,
                value=1000,
                source="secform4",
            ),
            InsiderTrade(
                ticker="META",
                company="Meta Platforms Inc.",
                insider_name="Mark Zuckerberg",
                trade_type="Sell",
                trade_date=date(2026, 6, 10),
                shares=10,
                price=500,
                value=5000,
                source="secform4",
            ),
        ]
    )
    repo = FeedRepository(engine)

    result = repo.search_entities("inc", limit=2)

    assert len(result.companies) <= 2


def test_search_entities_returns_entity_hit_dataclass(engine):
    """search_entities() results contain valid EntityHit instances."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    result = repo.search_entities("apple")

    for hit in result.companies:
        assert isinstance(hit, EntityHit)
        assert hit.kind in ("company", "insider")

    for hit in result.insiders:
        assert isinstance(hit, EntityHit)
        assert hit.kind == "insider"


# ---------------------------------------------------------------------------
# EntityHit and EntitySearchResults dataclasses
# ---------------------------------------------------------------------------


def test_entity_hit_is_frozen():
    """EntityHit must be a frozen dataclass."""
    from dataclasses import FrozenInstanceError

    hit = EntityHit(
        kind="company",
        label="Apple Inc.",
        identifier="AAPL",
        person="",
        market=FeedMarket.US,
    )
    with pytest.raises(FrozenInstanceError):
        hit.label = "other"  # type: ignore[misc]


def test_entity_search_results_is_frozen():
    """EntitySearchResults must be a frozen dataclass."""
    from dataclasses import FrozenInstanceError

    r = EntitySearchResults(companies=(), insiders=())
    with pytest.raises(FrozenInstanceError):
        r.companies = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# InvestigationBundle and load_investigation()
# ---------------------------------------------------------------------------


def test_investigation_bundle_is_frozen():
    """InvestigationBundle must be a frozen dataclass."""
    from dataclasses import FrozenInstanceError

    bundle = InvestigationBundle(detail=None, by_person=(), by_issuer=())
    with pytest.raises(FrozenInstanceError):
        bundle.by_person = ()  # type: ignore[misc]


def test_load_investigation_returns_bundle(engine):
    """load_investigation() returns InvestigationBundle wiring detail + person + issuer."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    # Retrieve the US record
    us_record = repo.detail("us:1")
    assert us_record is not None
    record = us_record.record

    bundle = load_investigation(repo, record)

    assert isinstance(bundle, InvestigationBundle)
    assert bundle.detail is not None
    assert bundle.detail.record.key == "us:1"
    # The anchor record itself is excluded from by_person and by_issuer
    person_keys = [r.key for r in bundle.by_person]
    issuer_keys = [r.key for r in bundle.by_issuer]
    assert "us:1" not in person_keys
    assert "us:1" not in issuer_keys


def test_load_investigation_excludes_anchor_key(engine):
    """load_investigation() excludes the anchor record from related lists."""
    # Seed a second US trade for the same person and issuer
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
            ),
            InsiderTrade(
                ticker="AAPL",
                company="Apple Inc.",
                insider_name="Tim Cook",
                insider_title="CEO",
                trade_type="Buy",
                trade_date=date(2026, 6, 8),
                filing_date=date(2026, 6, 9),
                shares=50,
                price=190,
                value=9_500,
                source="secform4",
            ),
        ]
    )
    repo = FeedRepository(engine)
    detail = repo.detail("us:1")
    assert detail is not None
    record = detail.record

    bundle = load_investigation(repo, record)

    # The anchor (us:1) must not appear in the related lists
    assert record.key not in [r.key for r in bundle.by_person]
    assert record.key not in [r.key for r in bundle.by_issuer]


def test_load_investigation_missing_detail_returns_none_detail(engine):
    """load_investigation() with a record whose key is not in DB returns detail=None."""
    _seed_all_markets(engine)
    repo = FeedRepository(engine)

    # Fabricate a record with a non-existent key
    ghost_record = FeedRecord(
        key="us:9999",
        market=FeedMarket.US,
        transaction_type="Sell",
        issuer="Ghost Co.",
        identifier="GHO",
        person="Nobody",
        role="",
        transaction_date=None,
        filing_date=None,
        quantity=None,
        price=None,
        value_display="",
        value_sort=None,
        currency="USD",
        source="secform4",
        source_url="",
    )

    bundle = load_investigation(repo, ghost_record)

    assert bundle.detail is None
    assert isinstance(bundle.by_person, tuple)
    assert isinstance(bundle.by_issuer, tuple)
