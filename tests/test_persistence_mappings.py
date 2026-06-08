"""Canonical-key and immutable mapping tests."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence.mappings import (
    congress_canonical_key,
    european_canonical_key,
    us_canonical_key,
)


def test_us_key_normalizes_identity_and_uses_share_bucket():
    left = InsiderTrade(
        ticker=" aapl ",
        insider_name=" Tim Cook ",
        trade_date=date(2026, 1, 1),
        shares=101,
    )
    right = InsiderTrade(
        ticker="AAPL",
        insider_name="tim cook",
        trade_date=date(2026, 1, 1),
        shares=104,
        source="other",
    )

    assert us_canonical_key(left) == us_canonical_key(right)


def test_congress_key_ignores_non_identity_descriptive_fields():
    left = CongressTrade(
        official_name="Jane Doe",
        doc_id="1",
        filing_date=date(2026, 1, 1),
        trade_date=date(2025, 12, 31),
        asset_description="Example",
        trade_type="Purchase",
    )
    right = CongressTrade.from_dict(
        {**left.to_dict(), "source_url": "https://new.test", "comment": "corrected"}
    )

    assert congress_canonical_key(left) == congress_canonical_key(right)


def test_european_undated_key_normalizes_identical_transaction_evidence():
    left = EuropeanInsiderTrade(
        isin=" nl0000009165 ",
        insider_name=" Jane Doe ",
        trade_date=None,
        trade_type="Buy",
        source=" AFM ",
        source_url="https://example.test/notice",
        volume=100.0,
        price=2.5,
    )
    right = EuropeanInsiderTrade(
        isin="NL0000009165",
        insider_name="jane doe",
        trade_date=None,
        trade_type="Buy",
        source="afm",
        source_url="https://example.test/notice",
        volume=100.0,
        price=2.5,
    )

    assert european_canonical_key(left) == european_canonical_key(right)


def test_european_dated_key_keeps_cross_source_coalescing():
    left = EuropeanInsiderTrade(
        isin="NL0000009165",
        insider_name="Jane Doe",
        trade_date=date(2026, 1, 1),
        trade_type="Buy",
        source="afm",
    )
    right = EuropeanInsiderTrade.from_dict(
        {
            **left.to_dict(),
            "source": "other",
            "source_url": "https://example.test/notice",
            "volume": 100.0,
        }
    )

    assert european_canonical_key(left) == european_canonical_key(right)
