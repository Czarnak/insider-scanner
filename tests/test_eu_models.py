"""Tests for European insider trade models and helpers."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.eu_models import EuropeanInsiderTrade, normalize_position


class TestNormalizePosition:
    def test_executive_role(self):
        assert normalize_position("Chief Executive Officer") == "Executive"

    def test_non_executive_role(self):
        assert normalize_position("Independent Director") == "Non-Executive"

    def test_unknown_role(self):
        assert normalize_position("Consultant") == "Other"


class TestEuropeanInsiderTrade:
    def test_roundtrip_serialisation(self):
        trade = EuropeanInsiderTrade(
            isin="GB0002875804",
            issuer_name="Example PLC",
            country="UK",
            regulatory_body="FCA",
            insider_name="Jane Doe",
            position="Executive",
            trade_date=date(2026, 2, 1),
            filing_date=date(2026, 2, 2),
            trade_type="Buy",
            instrument_type="Share",
            volume=100.0,
            price=2.5,
            currency="GBP",
            total_value=250.0,
            source="rns",
            source_url="https://example.com/announcement",
        )

        clone = EuropeanInsiderTrade.from_dict(trade.to_dict())

        assert clone == trade

    def test_compute_total_value(self):
        assert EuropeanInsiderTrade.compute_total_value(10.0, 5.5) == 55.0
        assert EuropeanInsiderTrade.compute_total_value(None, 5.5) is None
