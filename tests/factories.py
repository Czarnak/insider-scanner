"""Shared trade samples; tests override fields relevant to their scenario."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade


def us_trade(**overrides) -> InsiderTrade:
    values = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "insider_name": "Tim Cook",
        "insider_title": "CEO",
        "trade_type": "Sell",
        "trade_date": date(2026, 1, 5),
        "filing_date": date(2026, 1, 7),
        "shares": 105.0,
        "price": 200.0,
        "value": 21_000.0,
        "shares_owned_after": 500_000.0,
        "source": "secform4",
        "edgar_url": "",
        "is_congress": False,
        "congress_member": "",
    }
    values.update(overrides)
    return InsiderTrade(**values)


def congress_trade(**overrides) -> CongressTrade:
    values = {
        "official_name": "Jane Doe",
        "chamber": "House",
        "party": "I",
        "filing_date": date(2026, 2, 2),
        "doc_id": "20012345",
        "source_url": "https://example.test/ptr",
        "trade_date": date(2026, 1, 30),
        "asset_description": "Example Corp",
        "ticker": "EXM",
        "trade_type": "Purchase",
        "owner": "Spouse",
        "amount_range": "$1,001 - $15,000",
        "amount_low": 1001.0,
        "amount_high": 15_000.0,
        "comment": "Joint account",
        "source": "house",
    }
    values.update(overrides)
    return CongressTrade(**values)


def eu_trade(**overrides) -> EuropeanInsiderTrade:
    values = {
        "isin": "GB0002875804",
        "issuer_name": "Example PLC",
        "country": "UK",
        "regulatory_body": "FCA",
        "insider_name": "Jane Doe",
        "position": "Director",
        "trade_date": date(2026, 3, 4),
        "filing_date": date(2026, 3, 5),
        "trade_type": "Buy",
        "instrument_type": "Share",
        "volume": 100.0,
        "price": 2.5,
        "currency": "GBP",
        "total_value": 250.0,
        "source": "rns",
        "source_url": "https://example.test/rns",
    }
    values.update(overrides)
    return EuropeanInsiderTrade(**values)


def sec_trade(**overrides) -> InsiderTrade:
    values = {
        "shares": 10.0,
        "value": 2_000.0,
        "source": "sec_edgar",
        "edgar_url": "https://sec.test/aapl",
        "accession_number": "0000320193-26-000001",
        "sec_row_id": "non-derivative:0",
    }
    values.update(overrides)
    return us_trade(**values)
