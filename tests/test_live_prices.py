"""Opt-in live smoke tests for price providers."""

import os
from datetime import date, timedelta
import pytest

from insider_scanner.core.prices import YahooSource, TiingoSource, PriceBar

pytestmark = pytest.mark.live


def test_live_yahoo_smoke():
    source = YahooSource()
    end = date.today()
    start = end - timedelta(days=14)

    bars = source.fetch_daily("AAPL", start, end)

    assert isinstance(bars, list)
    if bars:
        assert isinstance(bars[0], PriceBar)
        assert bars[0].symbol == "AAPL"

        for bar in bars:
            assert bar.date >= start
            assert bar.date <= end


def test_live_tiingo_smoke():
    api_key = os.environ.get("TIINGO_API_KEY")
    if not api_key or not api_key.strip():
        pytest.skip("TIINGO_API_KEY not set")

    source = TiingoSource(api_key.strip())
    end = date.today()
    start = end - timedelta(days=14)

    bars = source.fetch_daily("AAPL", start, end)

    assert isinstance(bars, list)
    if bars:
        assert isinstance(bars[0], PriceBar)
        assert bars[0].symbol == "AAPL"

        for bar in bars:
            assert bar.date >= start
            assert bar.date <= end
