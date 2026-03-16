"""Tests for European trade merge/filter/export helpers."""

from __future__ import annotations

import json
from datetime import date

from insider_scanner.core.eu_merger import (
    DISPLAY_COLUMNS,
    eu_trades_to_dataframe,
    filter_eu_trades,
    merge_eu_trades,
    save_eu_results,
)
from insider_scanner.core.eu_models import EuropeanInsiderTrade


def _trade(**overrides) -> EuropeanInsiderTrade:
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
        volume=100.0,
        price=2.5,
        currency="GBP",
        total_value=250.0,
        source="rns",
        source_url="https://example.com/announcement",
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


class TestMergeEuTrades:
    def test_deduplicates_and_sorts(self):
        newer = _trade(trade_date=date(2026, 2, 3), insider_name="Alice")
        duplicate = _trade(source="bafin", country="DE", regulatory_body="BaFin")
        merged = merge_eu_trades([_trade()], [duplicate, newer])

        assert merged == [newer, _trade()]


class TestFilterEuTrades:
    def test_filters_by_country_type_value_and_date(self):
        trades = [
            _trade(),
            _trade(
                isin="NL0000009165",
                country="NL",
                regulatory_body="AFM",
                trade_type="Sell",
                total_value=50.0,
                trade_date=date(2026, 1, 1),
            ),
        ]

        filtered = filter_eu_trades(
            trades,
            country="UK",
            trade_type="Buy",
            min_value=100.0,
            since=date(2026, 2, 1),
        )

        assert filtered == [_trade()]


class TestEuTradesToDataFrame:
    def test_uses_display_column_order(self):
        df = eu_trades_to_dataframe([_trade()])
        assert list(df.columns) == DISPLAY_COLUMNS
        assert df.iloc[0]["issuer_name"] == "Example PLC"


class TestSaveEuResults:
    def test_writes_csv_and_json(self, tmp_path):
        out = save_eu_results([_trade()], label="eu_test", output_dir=tmp_path)

        assert out == tmp_path
        assert (tmp_path / "eu_test.csv").exists()

        payload = json.loads((tmp_path / "eu_test.json").read_text(encoding="utf-8"))
        assert payload[0]["isin"] == "GB0002875804"
