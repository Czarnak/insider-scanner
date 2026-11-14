"""Tests for secform4.com scraper (mocked HTTP)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import responses

from insider_scanner.core.secform4 import parse_secform4_html, scrape_ticker
from tests.html_samples import SECFORM4_HTML

# AAPL CIK (raw, not zero-padded)
AAPL_CIK = "320193"

# Fixture rows (filing dates):
#   KONDO CHRIS        – Sale,     trade 2025-11-07, filing 2025-11-12
#   Parekh Kevan       – Sale,     trade 2025-10-16, filing 2025-10-17
#   COOK TIMOTHY D     – Sale,     trade 2025-10-02, filing 2025-10-03
#   Pelosi Nancy       – Purchase, trade 2025-09-15, filing 2025-09-17


class TestParseSecform4:
    def test_parse_trades(self):
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        assert len(trades) == 4

    def test_ticker_assigned(self):
        """Ticker should come from the Symbol column, not the argument."""
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        assert all(t.ticker == "AAPL" for t in trades)

    def test_source_set(self):
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        assert all(t.source == "secform4" for t in trades)

    def test_trade_types(self):
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        types = [t.trade_type for t in trades]
        assert "Sell" in types
        assert "Buy" in types

    def test_ceo_trade(self):
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        ceo = [t for t in trades if "COOK" in t.insider_name]
        assert len(ceo) == 1
        assert ceo[0].shares == 129_963
        assert ceo[0].price == 256.81
        assert ceo[0].value == 33_375_723

    def test_trade_dates_parsed(self):
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        ceo = next(t for t in trades if "COOK" in t.insider_name)
        assert ceo.trade_date == date(2025, 10, 2)
        assert ceo.filing_date == date(2025, 10, 3)

    def test_insider_title_parsed(self):
        """Insider title comes from <span class="pos">."""
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        ceo = next(t for t in trades if "COOK" in t.insider_name)
        assert ceo.insider_title == "Chief Executive Officer"

    def test_company_parsed(self):
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        assert all(t.company == "Apple Inc." for t in trades)

    def test_edgar_url_extracted(self):
        """Each row should have a secform4.com filing link."""
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        assert all(
            t.edgar_url.startswith("https://www.secform4.com/filings/") for t in trades
        )
        ceo = next(t for t in trades if "COOK" in t.insider_name)
        assert "0001214156-25-000011" in ceo.edgar_url

    def test_shares_owned_ignores_ownership_span(self):
        """Shares Owned should be the number, not '15,098(Direct)'."""
        trades = parse_secform4_html(SECFORM4_HTML, "AAPL")
        kondo = next(t for t in trades if "KONDO" in t.insider_name)
        assert kondo.shares_owned_after == 15_098

    def test_empty_html(self):
        trades = parse_secform4_html("<html><body></body></html>", "TEST")
        assert trades == []


class TestScrapeSecform4:
    """Tests for scrape_ticker with CIK resolution mocked."""

    @responses.activate
    @patch("insider_scanner.core.edgar.resolve_cik_from_json", return_value=AAPL_CIK)
    def test_scrape_ticker_mocked(self, mock_cik):
        responses.add(
            responses.GET,
            f"https://www.secform4.com/insider-trading/{AAPL_CIK}.htm",
            body=SECFORM4_HTML,
            status=200,
        )
        trades = scrape_ticker("AAPL", use_cache=False)
        assert len(trades) == 4
        assert all(t.ticker == "AAPL" for t in trades)
        mock_cik.assert_called_once_with("AAPL", use_cache=False)

    @responses.activate
    @patch("insider_scanner.core.edgar.resolve_cik_from_json", return_value=AAPL_CIK)
    def test_scrape_404(self, mock_cik):
        responses.add(
            responses.GET,
            f"https://www.secform4.com/insider-trading/{AAPL_CIK}.htm",
            status=404,
        )
        trades = scrape_ticker("AAPL", use_cache=False)
        assert trades == []

    @patch("insider_scanner.core.edgar.resolve_cik_from_json", return_value=None)
    def test_scrape_cik_not_found(self, mock_cik):
        """If CIK cannot be resolved, return empty list."""
        trades = scrape_ticker("ZZZZ", use_cache=False)
        assert trades == []

    @responses.activate
    @patch("insider_scanner.core.edgar.resolve_cik_from_json", return_value=AAPL_CIK)
    def test_scrape_network_error(self, mock_cik):
        responses.add(
            responses.GET,
            f"https://www.secform4.com/insider-trading/{AAPL_CIK}.htm",
            body=ConnectionError("network error"),
        )
        trades = scrape_ticker("AAPL", use_cache=False)
        assert trades == []

    @responses.activate
    @patch("insider_scanner.core.edgar.resolve_cik_from_json", return_value=AAPL_CIK)
    def test_scrape_with_start_date(self, mock_cik):
        responses.add(
            responses.GET,
            f"https://www.secform4.com/insider-trading/{AAPL_CIK}.htm",
            body=SECFORM4_HTML,
            status=200,
        )
        trades = scrape_ticker("AAPL", use_cache=False, start_date=date(2025, 10, 17))
        # Only filing dates >= Oct 17: Kondo (11/12), Parekh (10/17)
        assert all(t.filing_date >= date(2025, 10, 17) for t in trades)
        assert len(trades) == 2

    @responses.activate
    @patch("insider_scanner.core.edgar.resolve_cik_from_json", return_value=AAPL_CIK)
    def test_scrape_with_end_date(self, mock_cik):
        responses.add(
            responses.GET,
            f"https://www.secform4.com/insider-trading/{AAPL_CIK}.htm",
            body=SECFORM4_HTML,
            status=200,
        )
        trades = scrape_ticker("AAPL", use_cache=False, end_date=date(2025, 10, 3))
        # Only filing dates <= Oct 3: Cook (10/03), Pelosi (09/17)
        assert all(t.filing_date <= date(2025, 10, 3) for t in trades)
        assert len(trades) == 2

    @responses.activate
    @patch("insider_scanner.core.edgar.resolve_cik_from_json", return_value=AAPL_CIK)
    def test_scrape_with_date_range(self, mock_cik):
        responses.add(
            responses.GET,
            f"https://www.secform4.com/insider-trading/{AAPL_CIK}.htm",
            body=SECFORM4_HTML,
            status=200,
        )
        trades = scrape_ticker(
            "AAPL",
            use_cache=False,
            start_date=date(2025, 10, 3),
            end_date=date(2025, 10, 17),
        )
        # Filing dates in range Oct 3–Oct 17: Cook (10/03), Parekh (10/17)
        assert len(trades) == 2

    @responses.activate
    @patch("insider_scanner.core.edgar.resolve_cik_from_json", return_value=AAPL_CIK)
    def test_url_uses_cik_not_ticker(self, mock_cik):
        """Verify the HTTP request goes to CIK-based URL, not ticker-based."""
        responses.add(
            responses.GET,
            f"https://www.secform4.com/insider-trading/{AAPL_CIK}.htm",
            body=SECFORM4_HTML,
            status=200,
        )
        scrape_ticker("AAPL", use_cache=False)
        assert len(responses.calls) == 1
        assert f"/{AAPL_CIK}.htm" in responses.calls[0].request.url
