"""Live integration tests that require internet access.

Run with: pytest -m live
Offline tests are selected by default locally and in CI.
"""

from __future__ import annotations

import pytest

from insider_scanner.core.fetch_outcome import FetchStatus

pytestmark = pytest.mark.live


def _assert_live_trades(trades):
    # A result cap is an explicit completeness warning, not a broken fetch.
    assert trades.status in {FetchStatus.COMPLETE, FetchStatus.PARTIAL}, trades.issues
    assert all(issue.reason == "result_limit_reached" for issue in trades.issues)
    assert trades, "An active source must return trades; empty failures must not pass."


class TestSecform4Live:
    def test_scrape_aapl(self):
        from insider_scanner.core.secform4 import scrape_ticker

        trades = scrape_ticker("AAPL", use_cache=False)
        _assert_live_trades(trades)
        assert trades[0].ticker == "AAPL"
        assert trades[0].source == "secform4"


class TestOpeninsiderLive:
    def test_scrape_aapl(self):
        from insider_scanner.core.openinsider import scrape_ticker

        trades = scrape_ticker("AAPL", use_cache=False)
        _assert_live_trades(trades)
        assert trades[0].source == "openinsider"

    def test_scrape_latest(self):
        from insider_scanner.core.openinsider import scrape_latest

        trades = scrape_latest(count=10, use_cache=False)
        _assert_live_trades(trades)


class TestEdgarLive:
    def test_resolve_cik_aapl(self):
        from insider_scanner.core.edgar import resolve_cik

        cik = resolve_cik("AAPL", use_cache=False)
        # AAPL's CIK is well-known: 0000320193
        assert cik == "0000320193"

    def test_resolve_cik_unknown(self):
        from insider_scanner.core.edgar import resolve_cik

        cik = resolve_cik("ZZZZZZZZZZ", use_cache=False)
        assert cik is None


class TestFullPipelineLive:
    def test_scan_merge_flag(self):
        from insider_scanner.core.merger import merge_trades
        from insider_scanner.core.openinsider import scrape_ticker as oi
        from insider_scanner.core.secform4 import scrape_ticker as sf4
        from insider_scanner.core.senate import flag_congress_trades

        sf4_trades = sf4("AAPL", use_cache=False)
        oi_trades = oi("AAPL", use_cache=False)
        _assert_live_trades(sf4_trades)
        _assert_live_trades(oi_trades)
        merged = merge_trades(sf4_trades, oi_trades)
        flag_congress_trades(merged)

        assert isinstance(merged, list)
        assert merged
        assert len(merged) <= len(sf4_trades) + len(oi_trades)
