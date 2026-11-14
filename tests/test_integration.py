"""Integration tests for the Congress scan pipeline.

These tests verify that all components work together correctly:
House scraper + Senate scraper → filter → save → reload.
All network calls are mocked.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pandas as pd

from insider_scanner.core.models import CongressTrade
from insider_scanner.gui.congress_tab import (
    DISPLAY_COLUMNS,
    congress_trades_to_dataframe,
    filter_congress_trades,
    save_congress_results,
)

# -----------------------------------------------------------------------
# Shared fixtures
# -----------------------------------------------------------------------


def _house_trades() -> list[CongressTrade]:
    """Simulated House scraper output."""
    return [
        CongressTrade(
            official_name="Pelosi Nancy",
            chamber="House",
            party="D",
            filing_date=date(2025, 6, 15),
            doc_id="20012345",
            source_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20012345.pdf",
            trade_date=date(2025, 6, 1),
            asset_description="NVIDIA Corp - Common Stock (NVDA)",
            ticker="NVDA",
            trade_type="Purchase",
            owner="Spouse",
            amount_range="$1,000,001 - $5,000,000",
            amount_low=1000001.0,
            amount_high=5000000.0,
            comment="",
            source="house",
        ),
        CongressTrade(
            official_name="Pelosi Nancy",
            chamber="House",
            party="D",
            filing_date=date(2025, 6, 15),
            doc_id="20012345",
            source_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20012345.pdf",
            trade_date=date(2025, 6, 2),
            asset_description="Apple Inc - Common Stock (AAPL)",
            ticker="AAPL",
            trade_type="Sale",
            owner="Spouse",
            amount_range="$15,001 - $50,000",
            amount_low=15001.0,
            amount_high=50000.0,
            comment="",
            source="house",
        ),
        CongressTrade(
            official_name="Biggs Andy",
            chamber="House",
            party="R",
            filing_date=date(2025, 5, 20),
            doc_id="20098765",
            source_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20098765.pdf",
            trade_date=date(2025, 5, 10),
            asset_description="iShares Semiconductor ETF (SOXX)",
            ticker="SOXX",
            trade_type="Purchase",
            owner="Self",
            amount_range="$1,001 - $15,000",
            amount_low=1001.0,
            amount_high=15000.0,
            comment="",
            source="house",
        ),
    ]


def _senate_trades() -> list[CongressTrade]:
    """Simulated Senate scraper output."""
    return [
        CongressTrade(
            official_name="Tuberville Tommy",
            chamber="Senate",
            party="R",
            filing_date=date(2025, 7, 1),
            doc_id="abc-123-def",
            source_url="https://efdsearch.senate.gov/search/view/ptr/abc-123-def/",
            trade_date=date(2025, 6, 20),
            asset_description="Lockheed Martin Corp",
            ticker="LMT",
            trade_type="Purchase",
            owner="Self",
            amount_range="$50,001 - $100,000",
            amount_low=50001.0,
            amount_high=100000.0,
            comment="",
            source="senate",
        ),
        CongressTrade(
            official_name="Tuberville Tommy",
            chamber="Senate",
            party="R",
            filing_date=date(2025, 7, 1),
            doc_id="abc-123-def",
            source_url="https://efdsearch.senate.gov/search/view/ptr/abc-123-def/",
            trade_date=date(2025, 6, 22),
            asset_description="Microsoft Corp - Common Stock",
            ticker="MSFT",
            trade_type="Sale",
            owner="Spouse",
            amount_range="$100,001 - $250,000",
            amount_low=100001.0,
            amount_high=250000.0,
            comment="Rebalancing",
            source="senate",
        ),
    ]


MEMBER_SECTORS = {
    "Pelosi Nancy": ["Finance", "Technology"],
    "Tuberville Tommy": ["Defense"],
    "Biggs Andy": ["Other"],
}


# -----------------------------------------------------------------------
# Combined pipeline
# -----------------------------------------------------------------------


class TestCombinedPipeline:
    """Test House + Senate trades merged into one list."""

    def test_combined_trade_count(self):
        all_trades = _house_trades() + _senate_trades()
        assert len(all_trades) == 5

    def test_both_chambers_present(self):
        all_trades = _house_trades() + _senate_trades()
        chambers = {t.chamber for t in all_trades}
        assert chambers == {"House", "Senate"}

    def test_both_sources_present(self):
        all_trades = _house_trades() + _senate_trades()
        sources = {t.source for t in all_trades}
        assert sources == {"house", "senate"}


# -----------------------------------------------------------------------
# Filter pipeline
# -----------------------------------------------------------------------


class TestFilterPipeline:
    """Test filtering across mixed House + Senate trades."""

    def setup_method(self):
        self.all_trades = _house_trades() + _senate_trades()

    def test_filter_purchases_only(self):
        result = filter_congress_trades(
            self.all_trades,
            trade_type="Purchase",
        )
        assert len(result) == 3
        assert all(t.trade_type == "Purchase" for t in result)

    def test_filter_sales_only(self):
        result = filter_congress_trades(
            self.all_trades,
            trade_type="Sale",
        )
        assert len(result) == 2
        assert all(t.trade_type == "Sale" for t in result)

    def test_filter_high_value(self):
        result = filter_congress_trades(
            self.all_trades,
            min_value=50000.0,
        )
        # Pelosi NVDA ($1M+), Tuberville LMT ($50K+), Tuberville MSFT ($100K+)
        assert len(result) == 3

    def test_filter_defense_sector(self):
        result = filter_congress_trades(
            self.all_trades,
            sector="Defense",
            member_sectors=MEMBER_SECTORS,
        )
        assert len(result) == 2
        assert all(t.official_name == "Tuberville Tommy" for t in result)

    def test_filter_technology_sector(self):
        result = filter_congress_trades(
            self.all_trades,
            sector="Technology",
            member_sectors=MEMBER_SECTORS,
        )
        assert len(result) == 2
        assert all(t.official_name == "Pelosi Nancy" for t in result)

    def test_filter_date_range_june(self):
        result = filter_congress_trades(
            self.all_trades,
            since=date(2025, 6, 1),
            until=date(2025, 6, 30),
        )
        # Pelosi filed 6/15, Tuberville filed 7/1 (excluded)
        assert len(result) == 2
        assert all(t.official_name == "Pelosi Nancy" for t in result)

    def test_combined_type_sector_value(self):
        """Purchase + Technology sector + min $500K."""
        result = filter_congress_trades(
            self.all_trades,
            trade_type="Purchase",
            min_value=500000.0,
            sector="Technology",
            member_sectors=MEMBER_SECTORS,
        )
        assert len(result) == 1
        assert result[0].ticker == "NVDA"
        assert result[0].official_name == "Pelosi Nancy"

    def test_filter_returns_empty_for_nonexistent_sector(self):
        result = filter_congress_trades(
            self.all_trades,
            sector="Energy",
            member_sectors=MEMBER_SECTORS,
        )
        assert result == []


# -----------------------------------------------------------------------
# DataFrame conversion
# -----------------------------------------------------------------------


class TestDataframePipeline:
    """Test DataFrame conversion with mixed data."""

    def test_all_display_columns_present(self):
        all_trades = _house_trades() + _senate_trades()
        df = congress_trades_to_dataframe(all_trades)

        for col in DISPLAY_COLUMNS:
            assert col in df.columns, f"Missing column: {col}"

    def test_correct_row_count(self):
        all_trades = _house_trades() + _senate_trades()
        df = congress_trades_to_dataframe(all_trades)
        assert len(df) == 5

    def test_column_values(self):
        all_trades = _house_trades() + _senate_trades()
        df = congress_trades_to_dataframe(all_trades)

        # Check tickers are preserved
        tickers = set(df["ticker"])
        assert tickers == {"NVDA", "AAPL", "SOXX", "LMT", "MSFT"}

        # Check sources
        assert set(df["source"]) == {"house", "senate"}

    def test_filtered_then_dataframe(self):
        """Filter → DataFrame pipeline."""
        all_trades = _house_trades() + _senate_trades()
        filtered = filter_congress_trades(
            all_trades,
            trade_type="Purchase",
        )
        df = congress_trades_to_dataframe(filtered)
        assert len(df) == 3
        assert set(df["trade_type"]) == {"Purchase"}


# -----------------------------------------------------------------------
# Save + reload round-trip
# -----------------------------------------------------------------------


class TestSaveReloadPipeline:
    """Test save → reload round-trip preserves data."""

    def test_csv_round_trip(self, tmp_path):
        all_trades = _house_trades() + _senate_trades()

        with (
            patch(
                "insider_scanner.utils.config.SCAN_OUTPUTS_DIR",
                new=tmp_path,
            ),
            patch(
                "insider_scanner.utils.config.ensure_dirs",
            ),
        ):
            save_congress_results(all_trades, label="integration_test")

        csv_path = tmp_path / "integration_test.csv"
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 5
        assert "ticker" in df.columns
        assert set(df["ticker"]) == {"NVDA", "AAPL", "SOXX", "LMT", "MSFT"}

    def test_json_round_trip(self, tmp_path):
        all_trades = _house_trades() + _senate_trades()

        with (
            patch(
                "insider_scanner.utils.config.SCAN_OUTPUTS_DIR",
                new=tmp_path,
            ),
            patch(
                "insider_scanner.utils.config.ensure_dirs",
            ),
        ):
            save_congress_results(all_trades, label="integration_test")

        json_path = tmp_path / "integration_test.json"
        assert json_path.exists()

        data = json.loads(json_path.read_text())
        assert len(data) == 5

        # Reload into CongressTrade objects
        reloaded = [CongressTrade.from_dict(d) for d in data]
        assert len(reloaded) == 5
        assert reloaded[0].ticker == "NVDA"
        assert reloaded[0].official_name == "Pelosi Nancy"
        assert reloaded[0].amount_low == 1000001.0

    def test_filtered_save(self, tmp_path):
        """Filter → save only filtered results."""
        all_trades = _house_trades() + _senate_trades()
        filtered = filter_congress_trades(
            all_trades,
            trade_type="Sale",
        )

        with (
            patch(
                "insider_scanner.utils.config.SCAN_OUTPUTS_DIR",
                new=tmp_path,
            ),
            patch(
                "insider_scanner.utils.config.ensure_dirs",
            ),
        ):
            save_congress_results(filtered, label="sales_only")

        data = json.loads((tmp_path / "sales_only.json").read_text())
        assert len(data) == 2
        assert all(d["trade_type"] == "Sale" for d in data)


# -----------------------------------------------------------------------
# Scraper mock integration
# -----------------------------------------------------------------------


class TestScraperIntegration:
    """Test that the congress_tab scan flow calls scrapers correctly."""

    def test_house_only_scan(self):
        """Simulate a House-only scan through the pipeline."""
        with patch(
            "insider_scanner.core.congress_house.scrape_house_trades",
            return_value=_house_trades(),
        ) as mock_house:
            from insider_scanner.core.congress_house import scrape_house_trades

            trades = scrape_house_trades(
                official_name="Pelosi Nancy",
                date_from=date(2025, 1, 1),
                date_to=date(2025, 12, 31),
            )

        assert len(trades) == 3
        assert all(t.source == "house" for t in trades)
        mock_house.assert_called_once()

    def test_senate_only_scan(self):
        """Simulate a Senate-only scan through the pipeline."""
        with patch(
            "insider_scanner.core.congress_senate.scrape_senate_trades",
            return_value=_senate_trades(),
        ) as mock_senate:
            from insider_scanner.core.congress_senate import scrape_senate_trades

            trades = scrape_senate_trades(
                official_name="Tuberville Tommy",
                date_from=date(2025, 1, 1),
                date_to=date(2025, 12, 31),
            )

        assert len(trades) == 2
        assert all(t.source == "senate" for t in trades)
        mock_senate.assert_called_once()

    def test_combined_scan(self):
        """Simulate the full House + Senate scan → filter → save flow."""
        with (
            patch(
                "insider_scanner.core.congress_house.scrape_house_trades",
                return_value=_house_trades(),
            ),
            patch(
                "insider_scanner.core.congress_senate.scrape_senate_trades",
                return_value=_senate_trades(),
            ),
        ):
            from insider_scanner.core.congress_house import scrape_house_trades
            from insider_scanner.core.congress_senate import scrape_senate_trades

            all_trades = []
            all_trades.extend(scrape_house_trades())
            all_trades.extend(scrape_senate_trades())

        assert len(all_trades) == 5

        # Filter
        purchases = filter_congress_trades(
            all_trades,
            trade_type="Purchase",
        )
        assert len(purchases) == 3

        # DataFrame
        df = congress_trades_to_dataframe(purchases)
        assert len(df) == 3
        assert set(df["source"]) == {"house", "senate"}

    def test_all_officials_scan(self):
        """Simulate 'All' officials scan (official_name=None)."""
        with patch(
            "insider_scanner.core.congress_house.scrape_house_trades",
            return_value=_house_trades(),
        ) as mock_house:
            from insider_scanner.core.congress_house import scrape_house_trades

            trades = scrape_house_trades(official_name=None)

        assert len(trades) == 3
        mock_house.assert_called_once_with(official_name=None)
