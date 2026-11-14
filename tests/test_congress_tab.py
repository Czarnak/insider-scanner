"""Tests for congress_tab helper functions (non-GUI)."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from insider_scanner.core.models import CongressTrade
from insider_scanner.gui.congress_tab import (
    DISPLAY_COLUMNS,
    SECTORS,
    _load_congress_names,
    _load_member_sectors,
    congress_trades_to_dataframe,
    filter_congress_trades,
    save_congress_results,
)

# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


def _make_trade(**overrides) -> CongressTrade:
    """Create a CongressTrade with sensible defaults, accepting overrides."""
    defaults = {
        "official_name": "Pelosi Nancy",
        "chamber": "House",
        "filing_date": date(2025, 3, 15),
        "doc_id": "doc123",
        "source_url": "https://example.com/ptr/123",
        "trade_date": date(2025, 3, 10),
        "asset_description": "Apple Inc - Common Stock (AAPL)",
        "ticker": "AAPL",
        "trade_type": "Purchase",
        "owner": "Spouse",
        "amount_range": "$15,001 - $50,000",
        "amount_low": 15001.0,
        "amount_high": 50000.0,
        "comment": "",
        "source": "house",
    }
    defaults.update(overrides)
    return CongressTrade(**defaults)


SAMPLE_TRADES = [
    _make_trade(
        official_name="Pelosi Nancy",
        trade_type="Purchase",
        ticker="AAPL",
        amount_low=15001.0,
        filing_date=date(2025, 3, 15),
        source="house",
    ),
    _make_trade(
        official_name="Tuberville Tommy",
        trade_type="Sale",
        ticker="MSFT",
        amount_low=50001.0,
        filing_date=date(2025, 4, 1),
        chamber="Senate",
        source="senate",
    ),
    _make_trade(
        official_name="Pelosi Nancy",
        trade_type="Purchase",
        ticker="NVDA",
        amount_low=100001.0,
        filing_date=date(2025, 5, 20),
        source="house",
    ),
    _make_trade(
        official_name="Biggs Andy",
        trade_type="Exchange",
        ticker="BND",
        amount_low=1001.0,
        filing_date=date(2025, 2, 10),
        source="house",
    ),
]

MEMBER_SECTORS = {
    "Pelosi Nancy": ["Finance", "Technology"],
    "Tuberville Tommy": ["Defense"],
    "Biggs Andy": ["Other"],
}


# -----------------------------------------------------------------------
# SECTORS constant
# -----------------------------------------------------------------------


class TestSectors:
    def test_starts_with_all(self):
        assert SECTORS[0] == "All"

    def test_contains_key_sectors(self):
        for s in (
            "Defense",
            "Energy",
            "Finance",
            "Technology",
            "Healthcare",
            "Industrials",
            "Other",
        ):
            assert s in SECTORS


# -----------------------------------------------------------------------
# _load_congress_names
# -----------------------------------------------------------------------


class TestLoadCongressNames:
    def test_loads_from_file(self, tmp_path):
        data = [
            {"official_name": "Pelosi Nancy"},
            {"official_name": "Tuberville Tommy"},
        ]
        f = tmp_path / "congress_members.json"
        f.write_text(json.dumps(data))

        with patch("insider_scanner.utils.config.CONGRESS_FILE", new=f):
            names = _load_congress_names()

        assert names[0] == "All"
        assert "Pelosi Nancy" in names
        assert "Tuberville Tommy" in names

    def test_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.json"
        with patch("insider_scanner.utils.config.CONGRESS_FILE", new=f):
            names = _load_congress_names()
        assert names == ["All"]


# -----------------------------------------------------------------------
# _load_member_sectors
# -----------------------------------------------------------------------


class TestLoadMemberSectors:
    def test_loads_sectors(self, tmp_path):
        data = [
            {"official_name": "Pelosi Nancy", "sector": ["Finance", "Technology"]},
            {"official_name": "Biggs Andy", "sector": "Other"},
        ]
        f = tmp_path / "congress_members.json"
        f.write_text(json.dumps(data))

        with patch("insider_scanner.utils.config.CONGRESS_FILE", new=f):
            mapping = _load_member_sectors()

        assert mapping["Pelosi Nancy"] == ["Finance", "Technology"]
        # String sector gets wrapped in list
        assert mapping["Biggs Andy"] == ["Other"]

    def test_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.json"
        with patch("insider_scanner.utils.config.CONGRESS_FILE", new=f):
            mapping = _load_member_sectors()
        assert mapping == {}


# -----------------------------------------------------------------------
# congress_trades_to_dataframe
# -----------------------------------------------------------------------


class TestCongressTradesToDataframe:
    def test_converts(self):
        df = congress_trades_to_dataframe(SAMPLE_TRADES)
        assert len(df) == 4
        assert "official_name" in df.columns
        assert "ticker" in df.columns

    def test_empty(self):
        df = congress_trades_to_dataframe([])
        assert df.empty


# -----------------------------------------------------------------------
# filter_congress_trades
# -----------------------------------------------------------------------


class TestFilterCongressTrades:
    def test_no_filters(self):
        result = filter_congress_trades(SAMPLE_TRADES)
        assert len(result) == 4

    def test_by_trade_type(self):
        result = filter_congress_trades(SAMPLE_TRADES, trade_type="Purchase")
        assert len(result) == 2
        assert all(t.trade_type == "Purchase" for t in result)

    def test_by_min_value(self):
        result = filter_congress_trades(SAMPLE_TRADES, min_value=50000.0)
        assert len(result) == 2
        assert all(t.amount_low >= 50000 for t in result)

    def test_by_date_since(self):
        result = filter_congress_trades(SAMPLE_TRADES, since=date(2025, 4, 1))
        assert len(result) == 2

    def test_by_date_until(self):
        result = filter_congress_trades(SAMPLE_TRADES, until=date(2025, 3, 15))
        assert len(result) == 2

    def test_by_date_range(self):
        result = filter_congress_trades(
            SAMPLE_TRADES,
            since=date(2025, 3, 1),
            until=date(2025, 4, 30),
        )
        assert len(result) == 2

    def test_by_sector(self):
        result = filter_congress_trades(
            SAMPLE_TRADES,
            sector="Defense",
            member_sectors=MEMBER_SECTORS,
        )
        assert len(result) == 1
        assert result[0].official_name == "Tuberville Tommy"

    def test_by_sector_multi(self):
        """Pelosi has both Finance and Technology sectors."""
        result = filter_congress_trades(
            SAMPLE_TRADES,
            sector="Technology",
            member_sectors=MEMBER_SECTORS,
        )
        assert len(result) == 2
        assert all(t.official_name == "Pelosi Nancy" for t in result)

    def test_sector_all_returns_everything(self):
        result = filter_congress_trades(
            SAMPLE_TRADES,
            sector="All",
            member_sectors=MEMBER_SECTORS,
        )
        assert len(result) == 4

    def test_sector_without_mapping(self):
        """Sector filter with no member_sectors dict has no effect."""
        result = filter_congress_trades(
            SAMPLE_TRADES,
            sector="Defense",
            member_sectors=None,
        )
        assert len(result) == 4

    def test_combined_filters(self):
        result = filter_congress_trades(
            SAMPLE_TRADES,
            trade_type="Purchase",
            min_value=50000.0,
            sector="Technology",
            member_sectors=MEMBER_SECTORS,
        )
        assert len(result) == 1
        assert result[0].ticker == "NVDA"

    def test_zero_min_value_ignored(self):
        result = filter_congress_trades(SAMPLE_TRADES, min_value=0)
        assert len(result) == 4


# -----------------------------------------------------------------------
# save_congress_results
# -----------------------------------------------------------------------


class TestSaveCongressResults:
    def test_saves_csv_and_json(self, tmp_path):
        with (
            patch(
                "insider_scanner.utils.config.SCAN_OUTPUTS_DIR",
                new=tmp_path,
            ),
            patch(
                "insider_scanner.utils.config.ensure_dirs",
            ),
        ):
            save_congress_results(SAMPLE_TRADES[:2], label="test_scan")

        assert (tmp_path / "test_scan.csv").exists()
        assert (tmp_path / "test_scan.json").exists()

        # Verify JSON content
        data = json.loads((tmp_path / "test_scan.json").read_text())
        assert len(data) == 2
        assert data[0]["ticker"] == "AAPL"

    def test_saves_empty(self, tmp_path):
        with (
            patch(
                "insider_scanner.utils.config.SCAN_OUTPUTS_DIR",
                new=tmp_path,
            ),
            patch(
                "insider_scanner.utils.config.ensure_dirs",
            ),
        ):
            save_congress_results([], label="empty_scan")

        assert (tmp_path / "empty_scan.csv").exists()


# -----------------------------------------------------------------------
# DISPLAY_COLUMNS
# -----------------------------------------------------------------------


class TestDisplayColumns:
    def test_columns_present_in_trade_dict(self):
        """All display columns should exist in CongressTrade.to_dict()."""
        trade = _make_trade()
        d = trade.to_dict()
        for col in DISPLAY_COLUMNS:
            assert col in d, f"Column '{col}' missing from CongressTrade.to_dict()"
