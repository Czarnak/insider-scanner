"""Tests for European source-specific parsing helpers."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.afm import _parse_nl_date, _parse_record as parse_afm_record
from insider_scanner.core.amf import (
    _map_trade_type as amf_map_trade_type,
    _parse_french_date,
    _parse_pdf_text,
    _parse_price,
)
from insider_scanner.core.bafin import (
    _map_instrument,
    _map_trade_type as bafin_map_trade_type,
    _parse_filing_date,
    _parse_result_table,
    _parse_trade_date,
    _row_to_trade,
)
from insider_scanner.core.rns_investegate import (
    _determine_trade_type,
    _parse_announcement,
    _parse_price_volume,
    _parse_trade_date as rns_parse_trade_date,
    _to_eu_trade,
    _RawRns,
)
from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.eu_scan import scrape_eu_trades_for_isin


def _sample_trade(**overrides) -> EuropeanInsiderTrade:
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
        volume=10.0,
        price=2.5,
        currency="GBP",
        total_value=25.0,
        source="rns",
        source_url="https://example.com/article",
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


class TestAfmParsing:
    def test_parse_nl_date(self):
        assert _parse_nl_date("01/02/2026") == date(2026, 2, 1)
        assert _parse_nl_date("2026-02-01") == date(2026, 2, 1)
        assert _parse_nl_date("") is None

    def test_parse_record(self):
        trade = parse_afm_record(
            {
                "ISIN": "NL0000009165",
                "managerName": "Jane Doe",
                "issuerName": "Example NV",
                "function": "uitvoerend bestuurder",
                "transactionDate": "2026-02-01T12:00:00",
                "publicationDate": "2026-02-02",
                "transactionType": "Koop",
                "instrumentType": "Share",
                "volume": "10,5",
                "price": "2,5",
            },
            "NL0000009165",
        )

        assert trade.country == "NL"
        assert trade.trade_type == "Buy"
        assert trade.total_value == 26.25


class TestAmfParsing:
    def test_parse_french_date_iso(self):
        assert _parse_french_date("2026-02-01") == date(2026, 2, 1)

    def test_parse_french_date_words(self):
        assert _parse_french_date("1 mars 2026") == date(2026, 3, 1)
        assert _parse_french_date("15 février 2026") == date(2026, 2, 15)

    def test_parse_french_date_invalid(self):
        assert _parse_french_date("") is None
        assert _parse_french_date("not-a-date") is None

    def test_parse_pdf_text_extracts_fields(self):
        text = (
            "NOM / FONCTION DE LA PERSONNE CONCERNEE : Jean Dupont, Directeur général\n"
            "NOM : Example SA\n"
            "DATE DE LA TRANSACTION : 2026-02-01\n"
            "NATURE DE LA TRANSACTION : Acquisition\n"
            "VOLUME : 10\n"
            "PRIX UNITAIRE : 12.5 EUR\n"
        )
        fields = _parse_pdf_text(text)

        assert fields.get("insider_name") == "Jean Dupont"
        assert fields.get("issuer_name") == "Example SA"
        assert fields.get("trade_type_raw") == "Acquisition"

    def test_map_trade_type(self):
        assert amf_map_trade_type("Acquisition") == "Buy"
        assert amf_map_trade_type("Achat") == "Buy"
        assert amf_map_trade_type("Cession") == "Sell"
        assert amf_map_trade_type("unknown") == "Other"

    def test_parse_price_eur(self):
        price, currency = _parse_price("12.5 EUR")
        assert price == 12.5
        assert currency == "EUR"

    def test_parse_price_symbol(self):
        price, currency = _parse_price("€9,99")
        assert price == 9.99
        assert currency == "EUR"


class TestBafinParsing:
    def test_parse_trade_date(self):
        assert _parse_trade_date("01.02.2026") == date(2026, 2, 1)
        assert _parse_trade_date("2026-02-01") == date(2026, 2, 1)
        assert _parse_trade_date("bad") is None

    def test_parse_filing_date(self):
        assert _parse_filing_date("02.02.2026 10:30:00") == date(2026, 2, 2)
        assert _parse_filing_date("bad") is None

    def test_map_trade_type(self):
        assert bafin_map_trade_type("Kauf") == "Buy"
        assert bafin_map_trade_type("Verkauf") == "Sell"
        assert bafin_map_trade_type("Sonstiges") == "Other"

    def test_map_instrument(self):
        assert _map_instrument("Aktie") == "Share"
        assert _map_instrument("Derivat") == "Derivative"

    def test_parse_result_table_empty(self):
        assert _parse_result_table("<html><body></body></html>") == []

    def test_parse_result_table_with_data(self):
        html = """
        <table>
          <tbody>
            <tr>
              <td><a href="/database/DealingsInfo/ergebnisListe.do?meldungId=99&emittentBafinId=1">Example AG</a></td>
              <td>12345</td>
              <td>DE0007236101</td>
              <td>Max Mustermann</td>
              <td>Vorstand</td>
              <td>Aktie</td>
              <td>Kauf</td>
              <td>01.02.2026</td>
              <td>XETRA</td>
              <td>02.02.2026 10:30:00</td>
            </tr>
          </tbody>
        </table>
        """
        rows = _parse_result_table(html)

        assert len(rows) == 1
        assert rows[0]["isin"] == "DE0007236101"
        assert rows[0]["insider_name"] == "Max Mustermann"
        assert rows[0]["trade_type_de"] == "Kauf"

    def test_row_to_trade(self):
        row = {
            "issuer_name": "Example AG",
            "bafin_id": "12345",
            "isin": "DE0007236101",
            "insider_name": "Max Mustermann",
            "position_de": "Vorstand",
            "instrument_de": "Aktie",
            "trade_type_de": "Kauf",
            "trade_date_raw": "01.02.2026",
            "place": "XETRA",
            "filing_raw": "02.02.2026 10:30:00",
            "meldung_id": "99",
            "detail_url": "https://example.com/trade",
        }
        trade = _row_to_trade(row)

        assert trade is not None
        assert trade.country == "DE"
        assert trade.trade_type == "Buy"
        assert trade.trade_date == date(2026, 2, 1)
        assert trade.filing_date == date(2026, 2, 2)


class TestRnsParsing:
    def test_parse_announcement_no_tables(self):
        assert _parse_announcement("<html></html>", "https://example.com") is None

    def test_parse_announcement_with_data(self):
        # Parser detects section context from rows where cells[0] is in ("1","2","3","4")
        # and len(cells) >= 2. Data rows have an empty/sub-section first cell.
        html = """
        <table>
          <tr><td>1</td><td></td></tr>
          <tr><td></td><td>Name</td><td>Jane Doe</td></tr>
          <tr><td>3</td><td></td></tr>
          <tr><td></td><td>Name</td><td>Example PLC</td></tr>
          <tr><td></td><td>Nature of the transaction</td><td>Purchase of shares</td></tr>
          <tr><td></td><td>Date of the transaction</td><td>31/01/2026</td></tr>
          <tr><td></td><td>Price</td><td>GBP 2.5</td></tr>
        </table>
        """
        raw = _parse_announcement(html, "https://example.com/article")

        assert raw is not None
        assert raw.insider_name == "Jane Doe"
        assert raw.issuer_name == "Example PLC"
        assert raw.trade_type_raw == "Purchase of shares"

    def test_determine_trade_type(self):
        assert _determine_trade_type("Purchase of shares") == "Buy"
        assert _determine_trade_type("Disposal of shares") == "Sell"
        assert _determine_trade_type("Grant of options") == "Other"

    def test_parse_price_volume_gbp(self):
        # _parse_price_volume detects currency from symbols (£ € $ ¥), not text codes
        price, volume, currency = _parse_price_volume("£2.50  1200")
        assert price == 2.5
        assert volume == 1200.0
        assert currency == "GBP"

    def test_parse_price_volume_eur(self):
        price, volume, currency = _parse_price_volume("€0.065  12345")
        assert price == 0.065
        assert volume == 12345.0
        assert currency == "EUR"

    def test_rns_parse_trade_date(self):
        assert rns_parse_trade_date("31 January 2026") == date(2026, 1, 31)
        assert rns_parse_trade_date("2026-01-31") == date(2026, 1, 31)
        assert rns_parse_trade_date("31/01/2026") == date(2026, 1, 31)

    def test_to_eu_trade(self):
        raw = _RawRns(
            insider_name="Jane Doe",
            issuer_name="Example PLC",
            instrument_isin="GB0002875804",
            trade_type_raw="Purchase of shares",
            trade_date_raw="31/01/2026",
            price_raw="GBP 2.50  1200",
            source_url="https://example.com/article",
        )
        trade = _to_eu_trade(raw, "GB0002875804")

        assert trade is not None
        assert trade.country == "UK"
        assert trade.trade_type == "Buy"
        assert trade.price == 2.5
        assert trade.total_value == 3000.0


class TestEuScanDispatch:
    def test_dispatches_only_selected_country(self, monkeypatch):
        uk_trade = _sample_trade()
        de_calls = []

        monkeypatch.setattr(
            "insider_scanner.core.rns_investegate.fetch_uk_trades",
            lambda isin, since=None, until=None: [uk_trade],
        )
        monkeypatch.setattr(
            "insider_scanner.core.bafin.fetch_de_trades",
            lambda isin, since=None, until=None: de_calls.append(isin) or [],
        )

        trades = scrape_eu_trades_for_isin(
            "GB0002875804",
            "UK",
            date(2026, 1, 1),
            date(2026, 2, 2),
        )

        assert trades == [uk_trade]
        assert de_calls == []
