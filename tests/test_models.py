"""Tests for InsiderTrade and CongressTrade dataclasses."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.models import CongressTrade, InsiderTrade


class TestInsiderTradeSECFields:
    """Tests for the SEC EDGAR native fields added to InsiderTrade."""

    def test_sec_fields_defaults(self):
        t = InsiderTrade(ticker="AAPL")
        assert t.accession_number == ""
        assert t.sec_row_id == ""
        assert t.form_type == ""
        assert t.cik_issuer == ""
        assert t.cik_insider == ""
        assert t.period_of_report is None
        assert t.is_amendment is False
        assert t.is_derivative is False
        assert t.document_sha256 == ""
        assert t.transaction_code == ""
        assert t.acquired_disposed == ""
        assert t.direct_or_indirect == ""
        assert t.sec_detail_json == ""

    def test_sec_fields_full_construction(self):
        t = InsiderTrade(
            ticker="MSFT",
            company="Microsoft Corp",
            insider_name="Satya Nadella",
            insider_title="CEO",
            trade_type="Sell",
            trade_date=date(2024, 3, 1),
            filing_date=date(2024, 3, 3),
            shares=10000.0,
            price=420.50,
            value=4205000.0,
            shares_owned_after=500000.0,
            source="edgar",
            edgar_url="https://www.sec.gov/Archives/edgar/data/789019/000078901924000001/",
            accession_number="0000789019-24-000001",
            sec_row_id="0000789019-24-000001_ndtc_1",
            form_type="4",
            cik_issuer="789019",
            cik_insider="1513142",
            period_of_report=date(2024, 2, 28),
            is_amendment=True,
            is_derivative=True,
            document_sha256="abc123def456",
            transaction_code="S",
            acquired_disposed="D",
            direct_or_indirect="D",
            sec_detail_json='{"footnotes": []}',
        )
        assert t.accession_number == "0000789019-24-000001"
        assert t.sec_row_id == "0000789019-24-000001_ndtc_1"
        assert t.form_type == "4"
        assert t.cik_issuer == "789019"
        assert t.cik_insider == "1513142"
        assert t.period_of_report == date(2024, 2, 28)
        assert t.is_amendment is True
        assert t.is_derivative is True
        assert t.document_sha256 == "abc123def456"
        assert t.transaction_code == "S"
        assert t.acquired_disposed == "D"
        assert t.direct_or_indirect == "D"
        assert t.sec_detail_json == '{"footnotes": []}'

    def test_to_dict_includes_all_sec_keys(self):
        t = InsiderTrade(
            ticker="TSLA",
            accession_number="0001318605-24-000010",
            sec_row_id="row_001",
            form_type="4",
            cik_issuer="1318605",
            cik_insider="1494730",
            period_of_report=date(2024, 5, 10),
            is_amendment=False,
            is_derivative=False,
            document_sha256="deadbeef",
            transaction_code="P",
            acquired_disposed="A",
            direct_or_indirect="D",
            sec_detail_json="{}",
        )
        d = t.to_dict()
        assert d["accession_number"] == "0001318605-24-000010"
        assert d["sec_row_id"] == "row_001"
        assert d["form_type"] == "4"
        assert d["cik_issuer"] == "1318605"
        assert d["cik_insider"] == "1494730"
        assert d["period_of_report"] == "2024-05-10"
        assert d["is_amendment"] is False
        assert d["is_derivative"] is False
        assert d["document_sha256"] == "deadbeef"
        assert d["transaction_code"] == "P"
        assert d["acquired_disposed"] == "A"
        assert d["direct_or_indirect"] == "D"
        assert d["sec_detail_json"] == "{}"

    def test_to_dict_period_of_report_none_serializes_to_empty_string(self):
        t = InsiderTrade(ticker="AAPL")
        d = t.to_dict()
        assert d["period_of_report"] == ""

    def test_to_dict_period_of_report_set_serializes_to_iso_string(self):
        t = InsiderTrade(ticker="AAPL", period_of_report=date(2024, 1, 15))
        d = t.to_dict()
        assert d["period_of_report"] == "2024-01-15"

    def test_roundtrip_with_sec_fields(self):
        original = InsiderTrade(
            ticker="NVDA",
            company="NVIDIA Corp",
            insider_name="Jensen Huang",
            insider_title="CEO",
            trade_type="Sell",
            trade_date=date(2024, 6, 1),
            filing_date=date(2024, 6, 3),
            shares=50000.0,
            price=900.0,
            value=45000000.0,
            shares_owned_after=1000000.0,
            source="edgar",
            accession_number="0001045810-24-000099",
            sec_row_id="0001045810-24-000099_ndtc_2",
            form_type="4",
            cik_issuer="1045810",
            cik_insider="1131928",
            period_of_report=date(2024, 5, 31),
            is_amendment=False,
            is_derivative=False,
            document_sha256="sha256abc",
            transaction_code="S",
            acquired_disposed="D",
            direct_or_indirect="D",
            sec_detail_json='{"note": "test"}',
        )
        restored = InsiderTrade.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_legacy_payload_no_sec_keys(self):
        """Legacy openinsider/secform4 dicts without SEC keys must load with defaults."""
        legacy = {
            "ticker": "AAPL",
            "company": "Apple Inc",
            "insider_name": "Tim Cook",
            "insider_title": "CEO",
            "trade_type": "Buy",
            "trade_date": "2023-01-10",
            "filing_date": "2023-01-12",
            "shares": 100.0,
            "price": 130.0,
            "value": 13000.0,
            "shares_owned_after": 5000.0,
            "source": "openinsider",
            "edgar_url": "",
            "is_congress": False,
            "congress_member": "",
        }
        t = InsiderTrade.from_dict(legacy)
        assert t.accession_number == ""
        assert t.sec_row_id == ""
        assert t.form_type == ""
        assert t.cik_issuer == ""
        assert t.cik_insider == ""
        assert t.period_of_report is None
        assert t.is_amendment is False
        assert t.is_derivative is False
        assert t.document_sha256 == ""
        assert t.transaction_code == ""
        assert t.acquired_disposed == ""
        assert t.direct_or_indirect == ""
        assert t.sec_detail_json == ""


class TestCongressTradeBasic:
    def test_defaults(self):
        t = CongressTrade()
        assert t.official_name == ""
        assert t.chamber == ""
        assert t.trade_type == "Other"
        assert t.amount_low == 0.0
        assert t.amount_high == 0.0
        assert t.filing_date is None
        assert t.trade_date is None

    def test_full_construction(self):
        t = CongressTrade(
            official_name="Nancy Pelosi",
            chamber="House",
            party="Democrat",
            filing_date=date(2026, 1, 23),
            doc_id="20033725",
            source_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20033725.pdf",
            trade_date=date(2026, 1, 15),
            asset_description="Apple Inc. - Common Stock (AAPL)",
            ticker="AAPL",
            trade_type="Purchase",
            owner="Spouse",
            amount_range="$1,001 - $15,000",
            amount_low=1001.0,
            amount_high=15000.0,
            comment="50 call options",
            source="house",
        )
        assert t.official_name == "Nancy Pelosi"
        assert t.chamber == "House"
        assert t.ticker == "AAPL"
        assert t.trade_type == "Purchase"
        assert t.owner == "Spouse"
        assert t.amount_low == 1001.0
        assert t.amount_high == 15000.0


class TestCongressTradeSerialisation:
    def test_to_dict(self):
        t = CongressTrade(
            official_name="Test Member",
            chamber="Senate",
            filing_date=date(2026, 2, 1),
            trade_date=date(2026, 1, 20),
            ticker="MSFT",
            trade_type="Sale",
            amount_range="$15,001 - $50,000",
            amount_low=15001.0,
            amount_high=50000.0,
            source="senate",
        )
        d = t.to_dict()
        assert d["official_name"] == "Test Member"
        assert d["chamber"] == "Senate"
        assert d["filing_date"] == "2026-02-01"
        assert d["trade_date"] == "2026-01-20"
        assert d["ticker"] == "MSFT"
        assert d["trade_type"] == "Sale"
        assert d["amount_low"] == 15001.0
        assert d["amount_high"] == 50000.0

    def test_to_dict_none_dates(self):
        d = CongressTrade().to_dict()
        assert d["filing_date"] == ""
        assert d["trade_date"] == ""

    def test_from_dict(self):
        d = {
            "official_name": "Test Member",
            "chamber": "House",
            "filing_date": "2026-02-01",
            "trade_date": "2026-01-20",
            "ticker": "GOOG",
            "trade_type": "Purchase",
            "amount_range": "$50,001 - $100,000",
            "amount_low": 50001.0,
            "amount_high": 100000.0,
            "owner": "Self",
            "source": "house",
        }
        t = CongressTrade.from_dict(d)
        assert t.official_name == "Test Member"
        assert t.filing_date == date(2026, 2, 1)
        assert t.trade_date == date(2026, 1, 20)
        assert t.ticker == "GOOG"
        assert t.amount_low == 50001.0

    def test_from_dict_empty(self):
        t = CongressTrade.from_dict({})
        assert t.official_name == ""
        assert t.filing_date is None
        assert t.amount_low == 0.0

    def test_roundtrip(self):
        original = CongressTrade(
            official_name="Roundtrip Test",
            chamber="Senate",
            party="Republican",
            filing_date=date(2026, 3, 15),
            trade_date=date(2026, 3, 10),
            ticker="NVDA",
            trade_type="Purchase",
            amount_range="$100,001 - $250,000",
            amount_low=100001.0,
            amount_high=250000.0,
            owner="Joint",
            doc_id="12345",
            source="senate",
        )
        restored = CongressTrade.from_dict(original.to_dict())
        assert restored.official_name == original.official_name
        assert restored.filing_date == original.filing_date
        assert restored.trade_date == original.trade_date
        assert restored.ticker == original.ticker
        assert restored.amount_low == original.amount_low
        assert restored.amount_high == original.amount_high


class TestParseAmountRange:
    def test_standard_range(self):
        assert CongressTrade.parse_amount_range("$1,001 - $15,000") == (1001.0, 15000.0)

    def test_large_range(self):
        assert CongressTrade.parse_amount_range("$1,000,001 - $5,000,000") == (
            1000001.0,
            5000000.0,
        )

    def test_mid_range(self):
        assert CongressTrade.parse_amount_range("$50,001 - $100,000") == (
            50001.0,
            100000.0,
        )

    def test_over_pattern(self):
        assert CongressTrade.parse_amount_range("Over $50,000,000") == (
            50000000.0,
            50000000.0,
        )

    def test_empty_string(self):
        assert CongressTrade.parse_amount_range("") == (0.0, 0.0)

    def test_whitespace(self):
        assert CongressTrade.parse_amount_range("  ") == (0.0, 0.0)

    def test_no_commas(self):
        assert CongressTrade.parse_amount_range("$1001 - $15000") == (1001.0, 15000.0)

    def test_invalid_text(self):
        assert CongressTrade.parse_amount_range("unknown") == (0.0, 0.0)

    def test_single_value(self):
        # No dash separator
        assert CongressTrade.parse_amount_range("$5,000") == (0.0, 0.0)
