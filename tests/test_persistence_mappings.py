"""Canonical-key and immutable mapping tests."""

from __future__ import annotations

from datetime import date
from typing import Any

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence.mappings import (
    congress_canonical_key,
    european_canonical_key,
    us_canonical_key,
    us_from_row,
    us_to_values,
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


# ---------------------------------------------------------------------------
# SEC identity branch in us_canonical_key
# ---------------------------------------------------------------------------


def _sec_trade(**overrides) -> InsiderTrade:
    """Minimal valid InsiderTrade with SEC identity fields set."""
    values: dict[str, Any] = {
        "ticker": "AAPL",
        "insider_name": "Tim Cook",
        "trade_date": date(2026, 1, 5),
        "shares": 100.0,
        "source": "sec_edgar",
        "accession_number": "0001234567-26-000001",
        "sec_row_id": "0001234567-26-000001:nonDerivative:0",
    }
    values.update(overrides)
    return InsiderTrade(**values)


def test_sec_identity_key_is_deterministic_regardless_of_fuzzy_fields():
    """Same accession+row_id → same key even when fuzzy fields differ."""
    trade_a = _sec_trade(
        ticker="AAPL",
        insider_name="Tim Cook",
        trade_date=date(2026, 1, 5),
        shares=100.0,
    )
    trade_b = _sec_trade(
        ticker="MSFT",  # different ticker
        insider_name="Satya Nadella",  # different name
        trade_date=date(2025, 6, 1),  # different date
        shares=9999.0,  # different shares
    )

    assert us_canonical_key(trade_a) == us_canonical_key(trade_b)


def test_sec_identity_keys_differ_for_different_accession_row_pairs():
    """Different (accession, row_id) → different canonical keys."""
    trade_a = _sec_trade(
        accession_number="0001234567-26-000001",
        sec_row_id="0001234567-26-000001:nonDerivative:0",
    )
    trade_b = _sec_trade(
        accession_number="0001234567-26-000002",
        sec_row_id="0001234567-26-000002:nonDerivative:0",
    )

    assert us_canonical_key(trade_a) != us_canonical_key(trade_b)


def test_sec_identity_strips_whitespace_from_accession_and_row_id():
    """Leading/trailing whitespace in identity fields must not change the key."""
    trade_clean = _sec_trade(
        accession_number="0001234567-26-000001",
        sec_row_id="0001234567-26-000001:nonDerivative:0",
    )
    trade_padded = _sec_trade(
        accession_number="  0001234567-26-000001  ",
        sec_row_id=" 0001234567-26-000001:nonDerivative:0 ",
    )

    assert us_canonical_key(trade_clean) == us_canonical_key(trade_padded)


def test_fuzzy_fallback_when_sec_fields_absent():
    """Trades without accession+row_id still collide on fuzzy identity."""
    left = InsiderTrade(
        ticker=" aapl ",
        insider_name=" Tim Cook ",
        trade_date=date(2026, 1, 1),
        shares=101,
        accession_number="",
        sec_row_id="",
    )
    right = InsiderTrade(
        ticker="AAPL",
        insider_name="tim cook",
        trade_date=date(2026, 1, 1),
        shares=104,
        source="other",
        accession_number="",
        sec_row_id="",
    )

    assert us_canonical_key(left) == us_canonical_key(right)


def test_fuzzy_fallback_when_only_one_sec_field_set():
    """Partial SEC identity (only one field) must NOT trigger the SEC branch."""
    only_accession = InsiderTrade(
        ticker="AAPL",
        insider_name="Tim Cook",
        trade_date=date(2026, 1, 1),
        shares=100.0,
        accession_number="0001234567-26-000001",
        sec_row_id="",  # missing
    )
    only_row_id = InsiderTrade(
        ticker="AAPL",
        insider_name="Tim Cook",
        trade_date=date(2026, 1, 1),
        shares=100.0,
        accession_number="",  # missing
        sec_row_id="0001234567-26-000001:nonDerivative:0",
    )
    no_sec = InsiderTrade(
        ticker="AAPL",
        insider_name="Tim Cook",
        trade_date=date(2026, 1, 1),
        shares=100.0,
    )

    # All three should collide via the fuzzy key
    assert us_canonical_key(only_accession) == us_canonical_key(no_sec)
    assert us_canonical_key(only_row_id) == us_canonical_key(no_sec)


def test_us_to_values_includes_sec_fields():
    """us_to_values must serialise new SEC columns into the row dict."""
    trade = _sec_trade(
        form_type="4",
        cik_issuer="0000320193",
        cik_insider="0001513925",
        is_amendment=True,
        is_derivative=False,
        transaction_code="S",
        acquired_disposed="D",
        direct_or_indirect="D",
        sec_detail_json='{"foo": 1}',
    )
    values = us_to_values(trade)

    assert values["accession_number"] == trade.accession_number
    assert values["sec_row_id"] == trade.sec_row_id
    assert values["form_type"] == "4"
    assert values["cik_issuer"] == "0000320193"
    assert values["cik_insider"] == "0001513925"
    assert values["is_amendment"] is True
    assert values["is_derivative"] is False
    assert values["transaction_code"] == "S"
    assert values["acquired_disposed"] == "D"
    assert values["direct_or_indirect"] == "D"
    assert values["sec_detail_json"] == '{"foo": 1}'


def test_us_from_row_reconstructs_sec_fields():
    """us_from_row must round-trip all SEC fields through a row-like mapping."""
    trade = _sec_trade(
        form_type="4",
        cik_issuer="0000320193",
        cik_insider="0001513925",
        period_of_report=date(2026, 1, 4),
        is_amendment=False,
        is_derivative=True,
        document_sha256="abc123",
        transaction_code="M",
        acquired_disposed="A",
        direct_or_indirect="I",
        sec_detail_json='{"bar": 2}',
    )
    row = us_to_values(trade)
    # us_from_row expects a mapping with the exact dataclass field names
    reconstructed = us_from_row(row)

    assert reconstructed.accession_number == trade.accession_number
    assert reconstructed.sec_row_id == trade.sec_row_id
    assert reconstructed.form_type == "4"
    assert reconstructed.cik_issuer == "0000320193"
    assert reconstructed.cik_insider == "0001513925"
    assert reconstructed.period_of_report == date(2026, 1, 4)
    assert reconstructed.is_amendment is False
    assert reconstructed.is_derivative is True
    assert reconstructed.document_sha256 == "abc123"
    assert reconstructed.transaction_code == "M"
    assert reconstructed.acquired_disposed == "A"
    assert reconstructed.direct_or_indirect == "I"
    assert reconstructed.sec_detail_json == '{"bar": 2}'
