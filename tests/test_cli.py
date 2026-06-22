"""Tests for CLI parsing and EU scan command wiring."""

from __future__ import annotations

from argparse import Namespace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from insider_scanner import cli
from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.services.sec_daily import SecDailyIngestionSummary
from insider_scanner.services.sec_downloads import SecDownloadProgress

PLACEHOLDER_USER_AGENT = "InsiderScanner/0.1 (research; contact@example.com)"


def _sec_summary(**overrides: object) -> SecDailyIngestionSummary:
    base: dict[str, object] = {
        "interval": DateInterval(date(2026, 6, 15), date(2026, 6, 15)),
        "dates_total": 1,
        "dates_completed": 1,
        "filings_discovered": 2,
        "filings_parsed": 2,
        "transactions_inserted": 2,
        "transactions_updated": 0,
        "transactions_skipped": 0,
        "failures": 0,
    }
    base.update(overrides)
    return SecDailyIngestionSummary(**base)  # type: ignore[arg-type]


class TestParseDateArg:
    def test_accepts_iso_date(self):
        assert cli._parse_date_arg("2025-03-15") == date(2025, 3, 15)

    def test_rejects_invalid_date(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["scan", "AAPL", "--since", "15-03-2025"])


class TestBuildParser:
    def test_uses_published_command_name(self):
        assert cli.build_parser().prog == "insider-scanner-cli"

    def test_help_lists_published_name_and_resolve_cik(self):
        help_text = cli.build_parser().format_help()

        assert help_text.startswith("usage: insider-scanner-cli")
        assert "resolve-cik" in help_text

    @pytest.mark.parametrize("command", ["resolve-cik", "cik"])
    def test_resolve_cik_command_and_legacy_alias(self, command):
        args = cli.build_parser().parse_args([command, "AAPL"])

        assert args.ticker == "AAPL"
        assert args.func is cli.cmd_resolve_cik

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

        class FakeEuropeanService:
            def scan(self, isin, **kwargs):
                seen["scrape"] = (isin, kwargs)
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
            ),
            SimpleNamespace(european=FakeEuropeanService()),
        )

        out = capsys.readouterr().out
        assert seen["scrape"] == (
            "GB0002875804",
            {
                "country": "UK",
                "start_date": date(2025, 1, 1),
                "end_date": date(2025, 1, 31),
                "use_cache": True,
            },
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


class TestSecParsers:
    def test_sec_daily_parser_wiring(self):
        args = cli.build_parser().parse_args(
            ["sec-daily", "--date", "2026-06-15", "--no-cleanup", "--quiet"]
        )

        assert args.command == "sec-daily"
        assert args.func is cli.cmd_sec_daily
        assert args.date == date(2026, 6, 15)
        assert args.no_cleanup is True
        assert args.quiet is True

    def test_sec_daily_defaults(self):
        args = cli.build_parser().parse_args(["sec-daily"])

        assert args.date is None
        assert args.no_cleanup is False
        assert args.quiet is False

    def test_sec_daily_rejects_invalid_date(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["sec-daily", "--date", "15-06-2026"])

    def test_sec_catchup_parser_wiring(self):
        args = cli.build_parser().parse_args(
            ["sec-catchup", "--since", "2026-06-01", "--until", "2026-06-15"]
        )

        assert args.command == "sec-catchup"
        assert args.func is cli.cmd_sec_catchup
        assert args.since == date(2026, 6, 1)
        assert args.until == date(2026, 6, 15)

    def test_sec_catchup_requires_both_dates(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["sec-catchup", "--since", "2026-06-01"])

    def test_sec_backfill_parser_wiring(self):
        args = cli.build_parser().parse_args(
            ["sec-backfill", "--confirm-full-backfill"]
        )

        assert args.command == "sec-backfill"
        assert args.func is cli.cmd_sec_backfill
        assert args.confirm_full_backfill is True


class TestMakeCliProgress:
    def test_quiet_returns_none(self):
        assert cli._make_cli_progress(quiet=True) is None

    def test_emits_per_date_line_to_stderr_only(self, capsys):
        callback = cli._make_cli_progress(quiet=False)
        assert callback is not None

        callback(
            SecDownloadProgress(
                current_date=date(2026, 6, 15),
                dates_completed=1,
                dates_total=3,
                discovered=5,
                downloaded=4,
                parsed=4,
                skipped=1,
                failed=0,
            )
        )

        captured = capsys.readouterr()
        assert captured.out == ""  # progress never pollutes stdout
        assert "2026-06-15" in captured.err
        assert "1/3" in captured.err


class TestCmdSecDaily:
    def test_builds_service_and_ingests_requested_date(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_build_sec_client", lambda: object())
        monkeypatch.setattr(cli, "_sec_cache_root", lambda: Path("cache"))
        seen: dict[str, object] = {}

        class FakeService:
            def ingest_date(self, day, *, on_progress=None):
                seen["ingest_date"] = (day, on_progress)
                return _sec_summary()

        class FakeServices:
            def make_sec_daily(self, **kwargs):
                seen["make"] = kwargs
                return FakeService()

        code = cli.cmd_sec_daily(
            Namespace(date=date(2026, 6, 15), no_cleanup=False, quiet=True),
            FakeServices(),
        )

        assert code == 0
        assert seen["ingest_date"][0] == date(2026, 6, 15)  # type: ignore[index]
        make_kwargs = seen["make"]
        assert make_kwargs["cleanup"] is True  # type: ignore[index]
        assert make_kwargs["cache_root"] == Path("cache")  # type: ignore[index]
        assert make_kwargs["checkpoint_path"] is None  # type: ignore[index]
        assert "SEC ingestion" in capsys.readouterr().out

    def test_no_cleanup_flag_disables_cleanup(self, monkeypatch):
        monkeypatch.setattr(cli, "_build_sec_client", lambda: object())
        monkeypatch.setattr(cli, "_sec_cache_root", lambda: Path("cache"))
        seen: dict[str, object] = {}

        class FakeService:
            def ingest_date(self, day, *, on_progress=None):
                return _sec_summary()

        class FakeServices:
            def make_sec_daily(self, **kwargs):
                seen["make"] = kwargs
                return FakeService()

        cli.cmd_sec_daily(
            Namespace(date=date(2026, 6, 15), no_cleanup=True, quiet=True),
            FakeServices(),
        )

        assert seen["make"]["cleanup"] is False  # type: ignore[index]

    def test_ua_misconfig_prints_friendly_error_and_returns_1(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            "insider_scanner.utils.config.SEC_USER_AGENT", PLACEHOLDER_USER_AGENT
        )
        constructed: list[object] = []

        class FakeServices:
            def make_sec_daily(self, **kwargs):
                constructed.append(kwargs)
                return object()

        code = cli.cmd_sec_daily(
            Namespace(date=date(2026, 6, 15), no_cleanup=False, quiet=True),
            FakeServices(),
        )

        assert code == 1
        assert constructed == []  # service never built on misconfig
        err = capsys.readouterr().err
        assert "SEC_USER_AGENT" in err


class TestCmdSecCatchup:
    def test_passes_inclusive_range_and_checkpoint(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_build_sec_client", lambda: object())
        monkeypatch.setattr(cli, "_sec_cache_root", lambda: Path("cache"))
        monkeypatch.setattr(
            cli, "_sec_checkpoint_path", lambda: Path("cache/ckpt.json")
        )
        seen: dict[str, object] = {}

        class FakeService:
            def ingest_range(self, since, until, *, on_progress=None):
                seen["range"] = (since, until)
                return _sec_summary()

        class FakeServices:
            def make_sec_daily(self, **kwargs):
                seen["make"] = kwargs
                return FakeService()

        code = cli.cmd_sec_catchup(
            Namespace(
                since=date(2026, 6, 1),
                until=date(2026, 6, 3),
                no_cleanup=True,
                quiet=True,
            ),
            FakeServices(),
        )

        assert code == 0
        assert seen["range"] == (date(2026, 6, 1), date(2026, 6, 3))
        assert seen["make"]["cleanup"] is False  # type: ignore[index]
        assert seen["make"]["checkpoint_path"] == Path("cache/ckpt.json")  # type: ignore[index]
        assert "SEC ingestion" in capsys.readouterr().out

    def test_invalid_range_is_rejected_before_building_client(
        self, monkeypatch, capsys
    ):
        built: list[bool] = []
        monkeypatch.setattr(cli, "_build_sec_client", lambda: built.append(True))

        code = cli.cmd_sec_catchup(
            Namespace(
                since=date(2026, 6, 15),
                until=date(2026, 6, 1),
                no_cleanup=False,
                quiet=True,
            ),
            SimpleNamespace(),
        )

        assert code == 2
        assert built == []  # validated before any client/service construction
        assert "range" in capsys.readouterr().err.lower()


class TestCmdSecBackfill:
    def test_refuses_without_confirmation(self, capsys):
        code = cli.cmd_sec_backfill(
            Namespace(confirm_full_backfill=False), SimpleNamespace()
        )

        assert code == 2
        assert "--confirm-full-backfill" in capsys.readouterr().err

    def test_with_confirmation_reports_session_7_deferral(self, capsys):
        code = cli.cmd_sec_backfill(
            Namespace(confirm_full_backfill=True), SimpleNamespace()
        )

        assert code == 0
        out = capsys.readouterr().out
        assert "sec-catchup" in out
