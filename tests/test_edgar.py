"""Tests for SEC EDGAR CIK resolver (mocked HTTP)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
import responses

from insider_scanner.core import edgar
from insider_scanner.core.edgar import (
    COMPANY_TICKERS_URL,
    build_daily_master_index_url,
    build_edgar_url_for_trade,
    build_filing_archive_url,
    get_filing_url,
    normalize_cik,
    parse_cik_from_html,
    quarter_for_date,
    resolve_cik,
    resolve_cik_from_json,
)
from insider_scanner.core.models import InsiderTrade
from tests.html_samples import EDGAR_CIK_HTML, EDGAR_CIK_NOT_FOUND_HTML

# Sample company_tickers.json payload (SEC format)
COMPANY_TICKERS_JSON = json.dumps(
    {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
    }
)


@pytest.fixture(autouse=True)
def _configured_sec_contact(monkeypatch):
    """Mocked SEC calls still pass through the real contact validation."""
    monkeypatch.setenv("SEC_USER_AGENT", "InsiderScanner tests tests@insider.test")


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
                (
                    "https://www.sec.gov/Archives/edgar/data/1000228/"
                    "0001190297-26-000004.txt"
                ),
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
    def test_reuses_parsed_directory_for_multiple_tickers(self, monkeypatch):
        calls = []

        def fetch(url, **kwargs):
            calls.append((url, kwargs))
            return COMPANY_TICKERS_JSON

        monkeypatch.setattr(edgar, "_ticker_map_cache", None)
        monkeypatch.setattr(edgar, "fetch_url", fetch)

        assert resolve_cik_from_json("AAPL") == "320193"
        assert resolve_cik_from_json("msft") == "789019"
        assert resolve_cik_from_json("MISSING") is None
        assert len(calls) == 1
        assert calls[0][1]["cache_dir"] == edgar.EDGAR_CACHE_DIR

    def test_directory_expires_and_tracks_cache_location(self, monkeypatch, tmp_path):
        now = [0.0]
        calls = []

        def fetch(url, **kwargs):
            calls.append(kwargs["cache_dir"])
            return COMPANY_TICKERS_JSON

        monkeypatch.setattr(edgar, "_ticker_map_cache", None)
        monkeypatch.setattr(edgar.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(edgar, "fetch_url", fetch)
        assert resolve_cik_from_json("AAPL") == "320193"
        now[0] = 86399
        assert resolve_cik_from_json("AAPL") == "320193"
        assert len(calls) == 1
        now[0] = 86400
        assert resolve_cik_from_json("AAPL") == "320193"
        assert len(calls) == 2
        monkeypatch.setattr(edgar, "EDGAR_CACHE_DIR", tmp_path)
        assert resolve_cik_from_json("AAPL") == "320193"
        assert calls[-1] == tmp_path
        assert len(calls) == 3

    def test_cache_bypass_reads_fresh_directory_each_time(self, monkeypatch):
        calls = []

        def fetch(url, **kwargs):
            calls.append(kwargs["cache_dir"])
            return COMPANY_TICKERS_JSON

        monkeypatch.setattr(edgar, "_ticker_map_cache", None)
        monkeypatch.setattr(edgar, "fetch_url", fetch)
        assert resolve_cik_from_json("AAPL") == "320193"
        assert resolve_cik_from_json("AAPL", use_cache=False) == "320193"
        assert resolve_cik_from_json("MSFT", use_cache=False) == "789019"
        assert calls == [edgar.EDGAR_CACHE_DIR, None, None]

    @pytest.mark.parametrize("payload", ["[]", "null", "not JSON"])
    def test_invalid_directory_is_a_miss_and_not_memoized(self, monkeypatch, payload):
        payloads = iter([payload, COMPANY_TICKERS_JSON])
        monkeypatch.setattr(edgar, "_ticker_map_cache", None)
        monkeypatch.setattr(edgar, "fetch_url", lambda *a, **k: next(payloads))
        assert resolve_cik_from_json("AAPL") is None
        assert resolve_cik_from_json("AAPL") == "320193"

    def test_bad_optional_entries_do_not_hide_valid_tickers(self, monkeypatch):
        payload = json.dumps(
            {
                "0": {"ticker": "BAD", "cik_str": True},
                "1": None,
                "2": {"ticker": 123, "cik_str": 456},
                "3": {"ticker": "AAPL", "cik_str": 320193},
            }
        )
        monkeypatch.setattr(edgar, "fetch_url", lambda *a, **k: payload)
        assert resolve_cik_from_json("AAPL", use_cache=False) == "320193"
        assert resolve_cik_from_json("BAD", use_cache=False) is None

    def test_programming_errors_propagate(self, monkeypatch):
        def broken_fetch(*args, **kwargs):
            raise RuntimeError("programming error")

        monkeypatch.setattr(edgar, "fetch_url", broken_fetch)
        with pytest.raises(RuntimeError, match="programming error"):
            resolve_cik_from_json("AAPL", use_cache=False)

    def test_sustained_block_stops_without_html_fallback(self, monkeypatch):
        calls = []

        def blocked_fetch(url, **kwargs):
            calls.append(url)
            raise edgar.SecBlockedError(600)

        monkeypatch.setattr(edgar, "fetch_url", blocked_fetch)
        with pytest.raises(edgar.SecBlockedError):
            resolve_cik("AAPL", use_cache=False)
        assert calls == [COMPANY_TICKERS_URL]

    def test_html_fallback_propagates_sustained_block(self, monkeypatch):
        def blocked_fetch(url, **kwargs):
            if url == COMPANY_TICKERS_URL:
                return COMPANY_TICKERS_JSON
            raise edgar.SecBlockedError(600)

        monkeypatch.setattr(edgar, "fetch_url", blocked_fetch)
        with pytest.raises(edgar.SecBlockedError):
            resolve_cik("UNKNOWN", use_cache=False)

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
