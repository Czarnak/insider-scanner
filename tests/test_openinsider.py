"""Tests for openinsider.com scraper (mocked HTTP)."""

from __future__ import annotations

from datetime import date

import responses
from bs4 import BeautifulSoup

from insider_scanner.core.openinsider import (
    parse_openinsider_html,
    scrape_latest,
    scrape_ticker,
)
from tests.html_samples import OPENINSIDER_HTML


class TestParseOpeninsider:
    def test_parse_trades(self):
        trades = parse_openinsider_html(OPENINSIDER_HTML)
        assert len(trades) == 3

    def test_source_set(self):
        trades = parse_openinsider_html(OPENINSIDER_HTML)
        assert all(t.source == "openinsider" for t in trades)

    def test_tickers_extracted(self):
        trades = parse_openinsider_html(OPENINSIDER_HTML)
        tickers = {t.ticker for t in trades}
        assert tickers == {"AAPL", "MSFT", "TSLA"}

    def test_company_names(self):
        trades = parse_openinsider_html(OPENINSIDER_HTML)
        companies = {t.company for t in trades}
        assert "Apple Inc" in companies
        assert "Microsoft Corp" in companies

    def test_trade_values(self):
        trades = parse_openinsider_html(OPENINSIDER_HTML)
        msft = [t for t in trades if t.ticker == "MSFT"]
        assert len(msft) == 1
        assert msft[0].value == 84_000_000
        assert msft[0].shares == 200_000
        assert msft[0].trade_type == "Sell"

    def test_real_filing_timestamps(self):
        trades = parse_openinsider_html(OPENINSIDER_HTML)
        assert [trade.filing_date for trade in trades] == [
            date(2025, 11, 17),
            date(2025, 11, 14),
            date(2025, 11, 10),
        ]

    def test_signed_sales_persist_with_purchases(self, persistence):
        trades = parse_openinsider_html(OPENINSIDER_HTML)

        result = persistence.us_trades.upsert(trades)

        assert result.inserted == 3
        for trade in trades:
            assert persistence.us_trades.query(
                trade.ticker,
                start_date=trade.filing_date,
                end_date=trade.filing_date,
            ) == [trade]
        assert [(trade.trade_type, trade.shares, trade.value) for trade in trades] == [
            ("Sell", 100_000, 18_550_000),
            ("Sell", 200_000, 84_000_000),
            ("Buy", 5_000, 1_550_000),
        ]

    def test_trade_type_classification(self):
        trades = parse_openinsider_html(OPENINSIDER_HTML)
        tsla = next(t for t in trades if t.ticker == "TSLA")
        assert tsla.trade_type == "Buy"

    def test_ticker_override(self):
        trades = parse_openinsider_html(OPENINSIDER_HTML, ticker="AAPL")
        # When ticker column is present in HTML, it should use that
        tickers = {t.ticker for t in trades}
        assert len(trades) == 3
        assert tickers == {"AAPL", "MSFT", "TSLA"}

    def test_ticker_override_used_when_column_missing(self):
        soup = BeautifulSoup(OPENINSIDER_HTML, "lxml")
        for row in soup.select("tr"):
            row.find_all(["th", "td"])[2].decompose()

        trades = parse_openinsider_html(str(soup), ticker="nvda")

        assert len(trades) == 3
        assert [trade.ticker for trade in trades] == ["NVDA", "NVDA", "NVDA"]
        assert [trade.insider_name for trade in trades] == [
            "Cook Timothy D",
            "Nadella Satya",
            "Tuberville Tommy",
        ]

    def test_empty_html(self):
        trades = parse_openinsider_html("<html><body></body></html>")
        assert trades == []


class TestScrapeOpeninsider:
    @responses.activate
    def test_scrape_ticker_mocked(self):
        responses.add(
            responses.GET,
            "http://openinsider.com/screener",
            body=OPENINSIDER_HTML,
            status=200,
        )
        trades = scrape_ticker("AAPL", use_cache=False)
        assert len(trades) == 3
        assert {trade.ticker for trade in trades} == {"AAPL", "MSFT", "TSLA"}

    @responses.activate
    def test_scrape_latest_mocked(self):
        responses.add(
            responses.GET,
            "http://openinsider.com/screener",
            body=OPENINSIDER_HTML,
            status=200,
        )
        trades = scrape_latest(count=50, use_cache=False)
        assert len(trades) == 3
        assert {trade.ticker for trade in trades} == {"AAPL", "MSFT", "TSLA"}

    @responses.activate
    def test_scrape_error(self):
        responses.add(
            responses.GET,
            "http://openinsider.com/screener",
            status=500,
        )
        trades = scrape_ticker("AAPL", use_cache=False)
        assert trades == []

    @responses.activate
    def test_scrape_with_date_range_url(self):
        """Verify that date params produce a custom date range in the URL."""
        responses.add(
            responses.GET,
            "http://openinsider.com/screener",
            body=OPENINSIDER_HTML,
            status=200,
        )
        trades = scrape_ticker(
            "AAPL",
            use_cache=False,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 12, 31),
        )
        # Verify request was made with date range params
        assert len(responses.calls) == 1
        url = responses.calls[0].request.url
        # scrape_ticker() uses filing-date params (fd/fdr) for custom range mode
        assert "fd=-1" in url
        assert "fdr=" in url
        assert "06%2F01%2F2025" in url or "06/01/2025" in url
        assert len(trades) == 3
        assert {trade.ticker for trade in trades} == {"AAPL", "MSFT", "TSLA"}

    @responses.activate
    def test_scrape_latest_with_dates(self):
        responses.add(
            responses.GET,
            "http://openinsider.com/screener",
            body=OPENINSIDER_HTML,
            status=200,
        )
        trades = scrape_latest(
            count=50,
            use_cache=False,
            start_date=date(2025, 1, 1),
        )
        assert len(trades) == 3
        assert {trade.ticker for trade in trades} == {"AAPL", "MSFT", "TSLA"}
        url = responses.calls[0].request.url
        assert "td=7" in url
