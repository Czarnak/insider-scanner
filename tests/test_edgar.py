"""Tests for SEC EDGAR CIK resolver (mocked HTTP)."""

from __future__ import annotations

from datetime import date
import json
from typing import Any

import pytest
import responses

from insider_scanner.core.edgar import (
    COMPANY_TICKERS_URL,
    build_daily_master_index_url,
    parse_cik_from_html,
    resolve_cik,
    resolve_cik_from_json,
    get_filing_url,
    build_edgar_url_for_trade,
    build_filing_archive_url,
    normalize_cik,
    quarter_for_date,
)
from insider_scanner.core.models import InsiderTrade
from tests.fixtures import EDGAR_CIK_HTML, EDGAR_CIK_NOT_FOUND_HTML

# Sample company_tickers.json payload (SEC format)
COMPANY_TICKERS_JSON = json.dumps(
    {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
    }
)


class TestNormalizeCik:
    @pytest.mark.parametrize(
        ("cik", "expected"),
        [
            ("320193", "0000320193"),
            (320193, "0000320193"),
            ("0000320193", "0000320193"),
        ],
    )
    def test_normalizes_valid_cik(self, cik: str | int, expected: str):
        assert normalize_cik(cik) == expected

    @pytest.mark.parametrize("cik", ["", "   ", "32A193", "12345678901"])
    def test_rejects_invalid_cik_values(self, cik: str):
        with pytest.raises(ValueError):
            normalize_cik(cik)

    @pytest.mark.parametrize("cik", [None, 320193.0, True, b"320193"])
    def test_rejects_wrong_cik_types(self, cik: Any):
        with pytest.raises(TypeError):
            normalize_cik(cik)


class TestEdgarDateHelpers:
    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2026, 1, 1), "QTR1"),
            (date(2026, 4, 1), "QTR2"),
            (date(2026, 7, 1), "QTR3"),
            (date(2026, 10, 1), "QTR4"),
        ],
    )
    def test_quarter_for_date(self, day: date, expected: str):
        assert quarter_for_date(day) == expected

    def test_builds_daily_master_index_url(self):
        assert build_daily_master_index_url(date(2026, 6, 15)) == (
            "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR2/"
            "master.20260615.idx"
        )

    @pytest.mark.parametrize("day", [None, "2026-06-15", 20260615])
    def test_rejects_wrong_date_types(self, day: Any):
        with pytest.raises(TypeError):
            quarter_for_date(day)
        with pytest.raises(TypeError):
            build_daily_master_index_url(day)


class TestFilingArchiveUrl:
    @pytest.mark.parametrize(
        ("index_path", "expected"),
        [
            (
                "edgar/data/1000228/0001190297-26-000004.txt",
                "https://www.sec.gov/Archives/edgar/data/1000228/"
                "0001190297-26-000004.txt",
            ),
            (
                "edgar/data/1000228/ownership.xml",
                "https://www.sec.gov/Archives/edgar/data/1000228/ownership.xml",
            ),
            (
                "edgar/data/1000228/filing.html",
                "https://www.sec.gov/Archives/edgar/data/1000228/filing.html",
            ),
        ],
    )
    def test_builds_filing_archive_url(self, index_path: str, expected: str):
        assert build_filing_archive_url(index_path) == expected

    @pytest.mark.parametrize(
        "index_path",
        [
            "",
            "/edgar/data/1000228/filing.txt",
            r"edgar\data\1000228\filing.txt",
            "edgar/data/../filing.txt",
            "edgar/data/1000228/./filing.txt",
            "C:/edgar/data/1000228/filing.txt",
            "edgar/data/C:/filing.txt",
            "edgar/data/1000228/filing.txt?download=1",
            "edgar/data/1000228/filing.txt#document",
            "edgar/data//filing.txt",
            "other/data/1000228/filing.txt",
        ],
    )
    def test_rejects_invalid_archive_paths(self, index_path: str):
        with pytest.raises(ValueError):
            build_filing_archive_url(index_path)

    @pytest.mark.parametrize("index_path", [None, 123, b"edgar/data/1/a.txt"])
    def test_rejects_wrong_archive_path_types(self, index_path: Any):
        with pytest.raises(TypeError):
            build_filing_archive_url(index_path)


class TestParseCik:
    def test_parse_valid(self):
        cik = parse_cik_from_html(EDGAR_CIK_HTML)
        assert cik == "0000320193"

    def test_parse_not_found(self):
        cik = parse_cik_from_html(EDGAR_CIK_NOT_FOUND_HTML)
        assert cik is None

    def test_parse_empty(self):
        cik = parse_cik_from_html("<html><body></body></html>")
        assert cik is None


class TestResolveCikFromJson:
    @responses.activate
    def test_resolve_aapl(self):
        responses.add(
            responses.GET, COMPANY_TICKERS_URL, body=COMPANY_TICKERS_JSON, status=200
        )
        cik = resolve_cik_from_json("AAPL", use_cache=False)
        assert cik == "320193"

    @responses.activate
    def test_resolve_case_insensitive(self):
        responses.add(
            responses.GET, COMPANY_TICKERS_URL, body=COMPANY_TICKERS_JSON, status=200
        )
        cik = resolve_cik_from_json("aapl", use_cache=False)
        assert cik == "320193"

    @responses.activate
    def test_resolve_not_found(self):
        responses.add(
            responses.GET, COMPANY_TICKERS_URL, body=COMPANY_TICKERS_JSON, status=200
        )
        cik = resolve_cik_from_json("ZZZZ", use_cache=False)
        assert cik is None

    @responses.activate
    def test_resolve_network_error(self):
        responses.add(responses.GET, COMPANY_TICKERS_URL, body=ConnectionError("fail"))
        cik = resolve_cik_from_json("AAPL", use_cache=False)
        assert cik is None

    @responses.activate
    def test_resolve_tsla(self):
        responses.add(
            responses.GET, COMPANY_TICKERS_URL, body=COMPANY_TICKERS_JSON, status=200
        )
        cik = resolve_cik_from_json("TSLA", use_cache=False)
        assert cik == "1318605"


class TestResolveCik:
    """resolve_cik() should use JSON primary, HTML fallback, and zero-pad."""

    @responses.activate
    def test_json_primary_success(self):
        """JSON resolves → returns zero-padded CIK, no HTML request made."""
        responses.add(
            responses.GET, COMPANY_TICKERS_URL, body=COMPANY_TICKERS_JSON, status=200
        )
        cik = resolve_cik("AAPL", use_cache=False)
        assert cik == "0000320193"
        # Only 1 request (JSON), no HTML fallback
        assert len(responses.calls) == 1
        assert "company_tickers.json" in responses.calls[0].request.url

    @responses.activate
    def test_json_miss_html_fallback(self):
        """JSON returns None → falls back to HTML scraping."""
        responses.add(
            responses.GET, COMPANY_TICKERS_URL, body=COMPANY_TICKERS_JSON, status=200
        )
        responses.add(
            responses.GET,
            "https://www.sec.gov/cgi-bin/browse-edgar",
            body=EDGAR_CIK_HTML,
            status=200,
        )
        assert resolve_cik("UNKNOWN_BUT_IN_HTML", use_cache=False) == "0000320193"
        # JSON miss → HTML fallback tried (2 requests total)
        assert len(responses.calls) == 2

    @responses.activate
    def test_both_fail(self):
        """Both JSON and HTML fail → returns None."""
        responses.add(responses.GET, COMPANY_TICKERS_URL, body=ConnectionError("fail"))
        responses.add(
            responses.GET,
            "https://www.sec.gov/cgi-bin/browse-edgar",
            body=EDGAR_CIK_NOT_FOUND_HTML,
            status=200,
        )
        cik = resolve_cik("ZZZZ", use_cache=False)
        assert cik is None


class TestFilingUrl:
    def test_url_format(self):
        url = get_filing_url("0000320193", count=40)
        assert "CIK=0000320193" in url
        assert "count=40" in url
        assert "type=4" in url


class TestBuildEdgarUrl:
    def test_build_url(self):
        from datetime import date

        trade = InsiderTrade(
            ticker="AAPL",
            insider_name="Cook Timothy",
            filing_date=date(2025, 11, 15),
        )
        url = build_edgar_url_for_trade(trade)
        assert "Cook+Timothy" in url
        assert "AAPL" in url
        assert "forms=4" in url

    def test_build_url_no_date(self):
        trade = InsiderTrade(ticker="AAPL", insider_name="Test Person")
        url = build_edgar_url_for_trade(trade)
        assert "Test+Person" in url
        assert "AAPL" in url
