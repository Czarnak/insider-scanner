"""Tests for European source-specific parsing helpers."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.afm import _parse_nl_date, _parse_record as parse_afm_record
from insider_scanner.core.amf import _parse_fr_date, _parse_record as parse_amf_record
from insider_scanner.core.bafin import (
    _COL_CURRENCY,
    _COL_FILING_DATE,
    _COL_INSIDER,
    _COL_INSTRUMENT,
    _COL_ISIN,
    _COL_ISSUER,
    _COL_POSITION,
    _COL_PRICE,
    _COL_TOTAL,
    _COL_TRADE_DATE,
    _COL_TRADE_TYPE,
    _COL_URL,
    _COL_VOLUME,
    _extract_csv_url,
    _parse_csv,
)
from insider_scanner.core.rns_investegate import (
    _parse_announcement,
    _parse_announcement_links,
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

        assert _parse_nl_date("01/02/2026") == date(2026, 2, 1)
        assert trade.country == "NL"
        assert trade.trade_type == "Buy"
        assert trade.total_value == 26.25


class TestAmfParsing:
    def test_parse_record(self):
        trade = parse_amf_record(
            {
                "codeIsin": "FR0000131104",
                "raisonSociale": "Example SA",
                "nomPrenom": "Jean Dupont",
                "qualite": "Directeur general",
                "dateTransaction": "2026-02-01",
                "dateDeclaration": "02/02/2026",
                "natureOperation": "Achat",
                "categorieFI": "Action",
                "quantite": "10",
                "prixUnitaire": "12.5",
            },
            "FR0000131104",
        )

        assert _parse_fr_date("02/02/2026") == date(2026, 2, 2)
        assert trade.country == "FR"
        assert trade.trade_type == "Buy"
        assert trade.total_value == 125.0


class TestBafinParsing:
    def test_extract_csv_url(self):
        html = '<a href="/database/DealingsInfo/downloadCsv.do?id=42">CSV</a>'
        assert (
            _extract_csv_url(html)
            == "https://portal.mvp.bafin.de/database/DealingsInfo/downloadCsv.do?id=42"
        )

    def test_parse_csv(self):
        header = ";".join(
            [
                _COL_FILING_DATE,
                _COL_ISSUER,
                _COL_ISIN,
                _COL_INSIDER,
                _COL_POSITION,
                _COL_TRADE_DATE,
                _COL_TRADE_TYPE,
                _COL_INSTRUMENT,
                _COL_VOLUME,
                _COL_PRICE,
                _COL_CURRENCY,
                _COL_TOTAL,
                _COL_URL,
            ]
        )
        row = ";".join(
            [
                "02.02.2026",
                "Example AG",
                "DE0007236101",
                "Max Mustermann",
                "Vorstand",
                "01.02.2026",
                "Kauf",
                "Aktie",
                "1.000,00",
                "12,50",
                "EUR",
                "",
                "https://example.com/trade",
            ]
        )

        trades = _parse_csv(f"{header}\n{row}\n", "DE0007236101")

        assert len(trades) == 1
        assert trades[0].country == "DE"
        assert trades[0].total_value == 12500.0


class TestRnsParsing:
    def test_parse_announcement_links(self):
        html = """
        <a href="/article.aspx?id=1">One</a>
        <a href="/article.aspx?id=1">Duplicate</a>
        <a href="https://www.investegate.co.uk/article.aspx?id=2">Two</a>
        """
        assert _parse_announcement_links(html) == [
            "https://www.investegate.co.uk/article.aspx?id=1",
            "https://www.investegate.co.uk/article.aspx?id=2",
        ]

    def test_parse_announcement(self):
        html = """
        <table>
          <tr><td>Name of issuer</td><td>Example PLC</td></tr>
          <tr><td>ISIN</td><td>GB0002875804</td></tr>
          <tr><td>Name of PDMR</td><td>Jane Doe</td></tr>
          <tr><td>Position</td><td>Chief Executive Officer</td></tr>
          <tr><td>Date of notification</td><td>01/02/2026</td></tr>
          <tr><td>Date of transaction</td><td>31/01/2026</td></tr>
          <tr><td>Nature of the transaction</td><td>Purchase of shares</td></tr>
          <tr><td>Description of financial instrument</td><td>Ordinary shares</td></tr>
          <tr><td>Volume</td><td>1,200</td></tr>
          <tr><td>Price</td><td>GBP 250p</td></tr>
        </table>
        """

        trades = _parse_announcement(html, "https://example.com/article")

        assert len(trades) == 1
        assert trades[0].country == "UK"
        assert trades[0].trade_type == "Buy"
        assert trades[0].price == 2.5


class TestEuScanDispatch:
    def test_dispatches_only_selected_country(self, monkeypatch):
        uk_trade = _sample_trade()
        de_calls = []

        monkeypatch.setattr(
            "insider_scanner.core.rns_investegate.scrape_rns_trades",
            lambda isin, date_from=None, date_to=None: [uk_trade],
        )
        monkeypatch.setattr(
            "insider_scanner.core.bafin.scrape_bafin_trades",
            lambda isin, date_from=None, date_to=None: de_calls.append(isin) or [],
        )

        trades = scrape_eu_trades_for_isin(
            "GB0002875804",
            "UK",
            date(2026, 1, 1),
            date(2026, 2, 2),
        )

        assert trades == [uk_trade]
        assert de_calls == []
