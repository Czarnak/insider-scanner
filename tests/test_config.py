"""Tests for config utilities."""

from __future__ import annotations

from insider_scanner.utils.config import load_eu_watchlist, load_watchlist


class TestLoadWatchlist:
    def test_load_basic(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("AAPL\nMSFT\nGOOGL\n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT", "GOOGL"]

    def test_uppercase_conversion(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("aapl\nmsft\n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT"]

    def test_skip_blank_lines(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("AAPL\n\n\nMSFT\n\n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT"]

    def test_skip_comments(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("# My watchlist\nAAPL\n# Tech stocks\nMSFT\n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT"]

    def test_strip_whitespace(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("  AAPL  \n  MSFT  \n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT"]

    def test_missing_file(self, tmp_path):
        result = load_watchlist(tmp_path / "nonexistent.txt")
        assert result == []

    def test_empty_file(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("")
        result = load_watchlist(f)
        assert result == []


class TestLoadEuWatchlist:
    def test_load_valid_isins(self, tmp_path):
        f = tmp_path / "isins.txt"
        f.write_text("gb0002875804\nNL0000009165\n")
        result = load_eu_watchlist(f)
        assert result == ["GB0002875804", "NL0000009165"]

    def test_skip_invalid_entries(self, tmp_path, caplog):
        f = tmp_path / "isins.txt"
        f.write_text("SHORT\nFR0000131104\n")
        result = load_eu_watchlist(f)
        assert result == ["FR0000131104"]
        assert "Skipping invalid ISIN" in caplog.text
