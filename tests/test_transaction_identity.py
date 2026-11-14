"""Raw identities preserve distinct transactions and support corrected reparses."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
from itertools import permutations

import pytest

from insider_scanner.core.merger import merge_trade_pair, merge_trades
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.core.trade_identity import us_trade_identity
from insider_scanner.persistence.mappings import (
    congress_canonical_key,
    us_canonical_key,
)
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    UpsertResult,
    UsTradeRepository,
)
from tests.factories import congress_trade, sec_trade, us_trade


@pytest.mark.parametrize(
    "changes",
    [
        {"trade_type": "Exercise"},
        {"trade_type": "Buy"},
        {"price": 200.01},
        {"shares": 104.0},
        {"shares": 106.0},
        {"trade_date": date(2026, 1, 6)},
        {"shares_missing": True},
        {"price_missing": True},
    ],
)
def test_distinct_scraper_transactions_remain_distinct_in_memory_and_storage(
    engine, changes
):
    original = us_trade()
    other = replace(original, **changes)

    assert us_trade_identity(original) != us_trade_identity(other)
    assert us_canonical_key(original) != us_canonical_key(other)
    assert len(merge_trades([original], [other])) == 2
    repository = UsTradeRepository(engine)
    assert repository.upsert([original, other]) == UpsertResult(inserted=2)
    assert len(repository.query("AAPL")) == 2


@pytest.mark.parametrize("shares", [14.0, 15.0, 24.0, 25.0, 35.0, 104.0, 105.0])
def test_no_quantity_rounding_boundary_can_collapse_neighbors(shares):
    original = us_trade(shares=shares)
    neighbor = replace(original, shares=shares + 1)

    assert us_trade_identity(original) != us_trade_identity(neighbor)
    assert us_canonical_key(original) != us_canonical_key(neighbor)
    assert len(merge_trades([original, neighbor])) == 2


def test_scraper_sales_match_across_sign_source_and_unicode_casefold():
    signed = us_trade(
        ticker=" aapl ",
        insider_name=" STRAẞE ",
        shares=-105,
        source="openinsider",
    )
    unsigned = us_trade(insider_name="strasse", shares=105.0, source="secform4")

    assert us_trade_identity(signed) == us_trade_identity(unsigned)
    assert us_canonical_key(signed) == us_canonical_key(unsigned)
    merged = merge_trades([signed], [unsigned])
    assert len(merged) == 1
    assert merged[0].shares == 105.0


@pytest.mark.parametrize(
    "changes",
    [
        {"filing_date": date(2026, 1, 8)},
        {"source": "openinsider"},
        {"edgar_url": "https://sec.test/second-filing"},
    ],
)
def test_undated_scraper_observations_retain_filing_evidence(changes):
    original = us_trade(trade_date=None, edgar_url="https://sec.test/first-filing")
    other = replace(original, **changes)

    assert us_canonical_key(original) != us_canonical_key(other)
    assert len(merge_trades([original, other])) == 2
    assert len(merge_trades([original, replace(original)])) == 1


def test_native_sec_rows_and_scraper_observations_retain_raw_identity(engine):
    original = sec_trade()
    repeated_lot = replace(original, sec_row_id="non-derivative:1")
    scraper = replace(original, accession_number="", sec_row_id="", source="secform4")
    amendment = replace(original, accession_number="0000320193-26-000002")
    records = [original, repeated_lot, scraper, amendment]

    assert len({us_canonical_key(trade) for trade in records}) == 4
    assert len(merge_trades(records)) == 4
    repository = UsTradeRepository(engine)
    assert repository.upsert(records) == UpsertResult(inserted=4)
    assert len(repository.query("AAPL")) == 4


def test_scraper_coalescing_is_associative_and_preserves_complementary_fields():
    records = [
        us_trade(company="Apple Inc.", insider_title="", value=0, value_missing=True),
        us_trade(company="", insider_title="CEO", source="openinsider", value=999),
        us_trade(
            company="Alternate Apple",
            insider_title="",
            value=1001,
            edgar_url="https://sec.test/filing",
            is_congress=True,
            congress_member="Jane Doe",
        ),
    ]
    originals = deepcopy(records)
    results = [merge_trades(list(order))[0] for order in permutations(records)]

    assert all(result == results[0] for result in results)
    assert results[0].company == "Apple Inc."
    assert results[0].insider_title == "CEO"
    assert results[0].value == 1001
    assert results[0].value_missing is False
    assert results[0].edgar_url == "https://sec.test/filing"
    assert results[0].is_congress is True
    assert results[0].congress_member == "Jane Doe"
    assert records == originals


def test_same_sec_row_accepts_smaller_values_and_removes_stale_fields(engine):
    original = sec_trade(
        price=12.5,
        value=125,
        insider_title="Stale role",
        document_sha256="zzzz",
        sec_detail_json='{"stale": true}',
        is_derivative=True,
        is_congress=True,
        congress_member="Jane Doe",
    )
    corrected = replace(
        original,
        price=12.05,
        value=0,
        value_missing=True,
        insider_title="",
        shares_owned_after=0,
        shares_owned_after_missing=True,
        document_sha256="aaaa",
        sec_detail_json="",
        is_derivative=False,
        edgar_url="",
        is_congress=False,
        congress_member="",
    )
    originals = deepcopy((original, corrected))
    expected = replace(
        corrected,
        edgar_url=original.edgar_url,
        is_congress=True,
        congress_member="Jane Doe",
    )

    assert merge_trade_pair(original, corrected) == expected
    assert merge_trades([original], [corrected]) == [expected]
    repository = UsTradeRepository(engine)
    assert repository.upsert([original]) == UpsertResult(inserted=1)
    assert repository.upsert([corrected]) == UpsertResult(updated=1)
    assert repository.query("AAPL") == [expected]
    assert repository.upsert([corrected]) == UpsertResult(skipped=1)
    assert (original, corrected) == originals


def test_unchanged_sec_upsert_does_not_serialize_records_to_rank_them(
    engine, monkeypatch
):
    repository = UsTradeRepository(engine)
    trade = sec_trade(sec_detail_json='{"large_document": "retained"}')
    repository.upsert([trade])

    def unexpected_serialization(_trade):
        pytest.fail("An identical re-ingest must not serialize trades for ranking")

    monkeypatch.setattr(InsiderTrade, "to_dict", unexpected_serialization)
    assert repository.upsert([replace(trade)]) == UpsertResult(skipped=1)


def test_congress_source_rows_preserve_identical_lots_and_allow_corrections(engine):
    first = congress_trade(source_row_id="1")
    second = replace(first, source_row_id="2")
    corrected = replace(
        first,
        party="D",
        amount_range="$15,001 - $50,000",
        amount_low=15001,
        amount_high=50000,
        comment="A corrected account",
        source_url="https://example.test/a-corrected-filing",
        asset_description="Corrected issuer name",
    )

    assert congress_canonical_key(first) != congress_canonical_key(second)
    assert congress_canonical_key(first) == congress_canonical_key(corrected)
    repository = CongressTradeRepository(engine)
    assert repository.upsert([first, second]) == UpsertResult(inserted=2)
    assert repository.upsert([corrected]) == UpsertResult(updated=1)
    records = repository.query(first.official_name)
    assert len(records) == 2
    assert {record.source_row_id for record in records} == {"1", "2"}
    assert (
        next(record for record in records if record.source_row_id == "1") == corrected
    )
    assert repository.upsert([corrected, second]) == UpsertResult(skipped=2)


def test_congress_row_identity_is_scoped_by_source_and_filing():
    first = congress_trade(source_row_id="1")
    others = [
        replace(first, doc_id="another-filing"),
        replace(first, source="senate"),
        replace(first, source_row_id="2"),
    ]

    assert len({congress_canonical_key(trade) for trade in [first, *others]}) == 4
    padded = replace(
        first, source=" HOUSE ", doc_id=f" {first.doc_id} ", source_row_id=" 1 "
    )
    assert congress_canonical_key(first) == congress_canonical_key(padded)


def test_congress_source_row_round_trips_and_legacy_exports_keep_default():
    trade = congress_trade(source_row_id="page:2:table:1:row:4")
    assert CongressTrade.from_dict(trade.to_dict()) == trade
    legacy = trade.to_dict()
    legacy.pop("source_row_id")
    assert CongressTrade.from_dict(legacy).source_row_id == ""
