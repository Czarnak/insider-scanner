"""Tests for trade merger, deduplication, filtering, and export."""

from __future__ import annotations

from copy import deepcopy
from datetime import date

from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.merger import (
    merge_trades,
    filter_trades,
    trades_to_dataframe,
    save_scan_results,
)


def _trade(
    ticker="AAPL",
    name="Cook Timothy",
    trade_type="Sell",
    trade_date=None,
    filing_date=None,
    shares=100_000,
    price=185.0,
    value=18_500_000,
    source="secform4",
    edgar_url="",
    is_congress=False,
) -> InsiderTrade:
    return InsiderTrade(
        ticker=ticker,
        insider_name=name,
        trade_type=trade_type,
        trade_date=trade_date or date(2025, 11, 15),
        filing_date=filing_date or (trade_date or date(2025, 11, 15)),
        shares=shares,
        price=price,
        value=value,
        source=source,
        edgar_url=edgar_url,
        is_congress=is_congress,
    )


class TestMergeTrades:
    def test_basic_merge(self):
        a = [_trade(source="secform4")]
        b = [_trade(ticker="MSFT", name="Nadella", source="openinsider")]
        merged = merge_trades(a, b)
        assert len(merged) == 2

    def test_deduplicate(self):
        a = [_trade(source="secform4")]
        b = [_trade(source="openinsider")]
        merged = merge_trades(a, b)
        assert len(merged) == 1

    def test_keep_richer_record(self):
        a = [_trade(source="secform4", edgar_url="")]
        b = [_trade(source="openinsider", edgar_url="https://sec.gov/filing/123")]
        merged = merge_trades(a, b)
        assert len(merged) == 1
        assert merged[0].edgar_url == "https://sec.gov/filing/123"

    def test_preserve_congress_flag(self):
        a = [_trade(source="secform4", is_congress=True)]
        b = [_trade(source="openinsider")]
        merged = merge_trades(a, b)
        assert merged[0].is_congress

    def test_sorted_descending(self):
        a = [_trade(trade_date=date(2025, 1, 1))]
        b = [_trade(ticker="MSFT", name="Nadella", trade_date=date(2025, 12, 1))]
        merged = merge_trades(a, b)
        assert merged[0].trade_date == date(2025, 12, 1)
        assert merged[1].trade_date == date(2025, 1, 1)

    def test_empty_inputs(self):
        merged = merge_trades([], [])
        assert merged == []

    def test_single_source(self):
        a = [_trade(), _trade(ticker="MSFT", name="Nadella")]
        merged = merge_trades(a)
        assert len(merged) == 2

    def test_merge_does_not_mutate_inputs(self):
        poorer = _trade(source="secform4")
        richer = _trade(
            source="edgar",
            edgar_url="https://sec.gov/filing/123",
            is_congress=True,
        )
        originals = deepcopy((poorer, richer))

        merge_trades([poorer], [richer])

        assert (poorer, richer) == originals


class TestFilterTrades:
    def _trades(self):
        return [
            _trade(
                ticker="AAPL", trade_type="Sell", value=18_500_000, is_congress=False
            ),
            _trade(ticker="MSFT", name="Nadella", trade_type="Buy", value=5_000_000),
            _trade(
                ticker="TSLA",
                name="Tuberville",
                trade_type="Buy",
                value=1_000_000,
                is_congress=True,
            ),
        ]

    def test_filter_by_ticker(self):
        result = filter_trades(self._trades(), ticker="AAPL")
        assert len(result) == 1
        assert result[0].ticker == "AAPL"

    def test_filter_by_type(self):
        result = filter_trades(self._trades(), trade_type="Buy")
        assert len(result) == 2

    def test_filter_by_min_value(self):
        result = filter_trades(self._trades(), min_value=10_000_000)
        assert len(result) == 1
        assert result[0].ticker == "AAPL"

    def test_filter_congress_only(self):
        result = filter_trades(self._trades(), congress_only=True)
        assert len(result) == 1
        assert result[0].is_congress

    def test_filter_since_date(self):
        trades = [
            _trade(filing_date=date(2025, 1, 1)),
            _trade(ticker="MSFT", name="N", filing_date=date(2025, 6, 1)),
            _trade(ticker="TSLA", name="T", filing_date=date(2025, 12, 1)),
        ]
        result = filter_trades(trades, since=date(2025, 6, 1))
        assert len(result) == 2

    def test_filter_until_date(self):
        trades = [
            _trade(filing_date=date(2025, 1, 1)),
            _trade(ticker="MSFT", name="N", filing_date=date(2025, 6, 1)),
            _trade(ticker="TSLA", name="T", filing_date=date(2025, 12, 1)),
        ]
        result = filter_trades(trades, until=date(2025, 6, 1))
        assert len(result) == 2

    def test_filter_date_range(self):
        trades = [
            _trade(filing_date=date(2025, 1, 1)),
            _trade(ticker="MSFT", name="N", filing_date=date(2025, 6, 1)),
            _trade(ticker="TSLA", name="T", filing_date=date(2025, 12, 1)),
        ]
        result = filter_trades(trades, since=date(2025, 3, 1), until=date(2025, 9, 1))
        assert len(result) == 1
        assert result[0].ticker == "MSFT"

    def test_combined_filters(self):
        result = filter_trades(
            self._trades(),
            trade_type="Buy",
            min_value=2_000_000,
        )
        assert len(result) == 1
        assert result[0].ticker == "MSFT"

    def test_no_filters(self):
        result = filter_trades(self._trades())
        assert len(result) == 3


class TestTradesToDataframe:
    def test_basic(self):
        trades = [_trade(), _trade(ticker="MSFT", name="Nadella")]
        df = trades_to_dataframe(trades)
        assert len(df) == 2
        assert "ticker" in df.columns
        assert "value" in df.columns

    def test_empty(self):
        df = trades_to_dataframe([])
        assert len(df) == 0


class TestSaveScanResults:
    def test_save_creates_files(self, tmp_path):
        trades = [_trade()]
        save_scan_results(trades, label="test_scan", output_dir=tmp_path)
        assert (tmp_path / "test_scan.csv").exists()
        assert (tmp_path / "test_scan.json").exists()
