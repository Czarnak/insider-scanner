"""Tests for CLI parsing and EU scan command wiring."""

from __future__ import annotations

from argparse import Namespace
from datetime import date

import pytest

from insider_scanner import cli
from insider_scanner.core.eu_models import EuropeanInsiderTrade


class TestParseDateArg:
    def test_accepts_iso_date(self):
        assert cli._parse_date_arg("2025-03-15") == date(2025, 3, 15)

    def test_rejects_invalid_date(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["scan", "AAPL", "--since", "15-03-2025"])


class TestBuildParser:
    def test_eu_scan_parser_populates_expected_fields(self):
        args = cli.build_parser().parse_args(
            [
                "eu-scan",
                "GB0002875804",
                "--country",
                "UK",
                "--type",
                "Buy",
                "--min-value",
                "12345",
                "--since",
                "2025-01-01",
                "--until",
                "2025-02-01",
                "--watchlist",
                "--save",
            ]
        )

        assert args.command == "eu-scan"
        assert args.isin == "GB0002875804"
        assert args.country == "UK"
        assert args.type == "Buy"
        assert args.min_value == 12345.0
        assert args.since == date(2025, 1, 1)
        assert args.until == date(2025, 2, 1)
        assert args.watchlist is True
        assert args.save is True


class TestCmdEuScan:
    def test_requires_isin_or_watchlist(self, capsys):
        cli.cmd_eu_scan(
            Namespace(
                isin=None,
                watchlist=False,
                country="All",
                type=None,
                min_value=None,
                since=None,
                until=None,
                save=False,
            )
        )

        out = capsys.readouterr().out
        assert "Provide an ISIN or use --watchlist." in out

    def test_watchlist_empty_short_circuits(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "insider_scanner.utils.config.load_eu_watchlist",
            lambda: [],
        )

        cli.cmd_eu_scan(
            Namespace(
                isin=None,
                watchlist=True,
                country="All",
                type=None,
                min_value=None,
                since=None,
                until=None,
                save=False,
            )
        )

        out = capsys.readouterr().out
        assert "EU watchlist is empty." in out

    def test_scans_watchlist_filters_and_saves(self, monkeypatch, capsys):
        sample_trade = EuropeanInsiderTrade(
            isin="GB0002875804",
            issuer_name="British American Tobacco",
            country="UK",
            regulatory_body="FCA",
            insider_name="Jane Doe",
            position="Executive",
            trade_date=date(2025, 1, 2),
            filing_date=date(2025, 1, 3),
            trade_type="Buy",
            instrument_type="Share",
            volume=1000.0,
            price=250.0,
            currency="GBP",
            total_value=250000.0,
            source="rns",
            source_url="https://example.test/rns",
        )

        seen: dict[str, object] = {}

        def fake_scrape(isin, country, since, until):
            seen["scrape"] = (isin, country, since, until)
            return [sample_trade]

        def fake_filter(trades, **kwargs):
            seen["filter"] = kwargs
            return trades

        def fake_merge(*batches):
            seen["merge_len"] = len(batches)
            return list(batches[0])

        def fake_save(trades, label):
            seen["save"] = (trades, label)
            return "outputs/scans"

        monkeypatch.setattr(
            "insider_scanner.utils.config.load_eu_watchlist",
            lambda: ["GB0002875804"],
        )
        monkeypatch.setattr(
            "insider_scanner.core.eu_scan.scrape_eu_trades_for_isin",
            fake_scrape,
        )
        monkeypatch.setattr(
            "insider_scanner.core.eu_merger.filter_eu_trades",
            fake_filter,
        )
        monkeypatch.setattr(
            "insider_scanner.core.eu_merger.merge_eu_trades",
            fake_merge,
        )
        monkeypatch.setattr(
            "insider_scanner.core.eu_merger.save_eu_results",
            fake_save,
        )

        cli.cmd_eu_scan(
            Namespace(
                isin=None,
                watchlist=True,
                country="UK",
                type="Buy",
                min_value=1000.0,
                since=date(2025, 1, 1),
                until=date(2025, 1, 31),
                save=True,
            )
        )

        out = capsys.readouterr().out
        assert seen["scrape"] == (
            "GB0002875804",
            "UK",
            date(2025, 1, 1),
            date(2025, 1, 31),
        )
        assert seen["filter"] == {
            "country": "UK",
            "trade_type": "Buy",
            "min_value": 1000.0,
            "since": date(2025, 1, 1),
            "until": date(2025, 1, 31),
        }
        assert seen["merge_len"] == 1
        assert seen["save"][1] == "WATCHLIST_eu_scan"
        assert "Found 1 European insider trade(s)" in out
