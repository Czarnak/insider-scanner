from __future__ import annotations

import json
from argparse import Namespace
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from insider_scanner import cli
from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import InsiderTrade
from insider_scanner.services.context import open_persistence
from insider_scanner.services.european import EuropeanScanService
from insider_scanner.services.us import UsScanService

DEFAULT_MAX_LEGACY_FILE_SIZE_BYTES = 50 * 1024 * 1024


class FakeUsService:
    def __init__(self, trades):
        self.trades = trades
        self.calls = []

    def scan(self, ticker, **kwargs):
        self.calls.append(("scan", ticker, kwargs))
        return self.trades

    def latest(self, **kwargs):
        self.calls.append(("latest", kwargs))
        return self.trades


class FakeEuService:
    def __init__(self, trades):
        self.trades = trades
        self.calls = []

    def scan(self, isin, **kwargs):
        self.calls.append((isin, kwargs))
        return self.trades


def _services(us=None, eu=None):
    return SimpleNamespace(us=us, european=eu)


def test_scan_uses_us_service_then_preserves_filter_and_save(monkeypatch, capsys):
    trade = InsiderTrade(
        ticker="AAPL",
        insider_name="Jane",
        trade_type="Buy",
        filing_date=date(2025, 1, 3),
        shares=10,
        value=1000,
    )
    service = FakeUsService([trade])
    saved = {}
    monkeypatch.setattr(
        "insider_scanner.core.merger.save_scan_results",
        lambda trades, label: saved.update(trades=trades, label=label) or "out",
    )

    cli.cmd_scan(
        Namespace(
            ticker="aapl",
            since=date(2025, 1, 1),
            until=date(2025, 1, 31),
            no_cache=True,
            type="Buy",
            min_value=500,
            congress_only=False,
            save=True,
        ),
        _services(us=service),
    )

    assert service.calls == [
        (
            "scan",
            "AAPL",
            {
                "sources": ("secform4", "openinsider"),
                "start_date": date(2025, 1, 1),
                "end_date": date(2025, 1, 31),
                "use_cache": False,
            },
        )
    ]
    assert saved["label"] == "AAPL_scan"
    assert "Found 1 trades for AAPL" in capsys.readouterr().out


def test_latest_uses_us_service_with_count_dates_and_cache(capsys):
    service = FakeUsService([])
    cli.cmd_latest(
        Namespace(
            count=25,
            since=None,
            until=None,
            no_cache=False,
            save=False,
        ),
        _services(us=service),
    )

    assert service.calls == [
        (
            "latest",
            {
                "count": 25,
                "sources": ("openinsider",),
                "start_date": None,
                "end_date": None,
                "use_cache": True,
            },
        )
    ]
    assert "Latest 0 insider trades" in capsys.readouterr().out


def test_eu_scan_uses_service_for_each_watchlist_identifier(monkeypatch, capsys):
    trade = EuropeanInsiderTrade(
        isin="GB0002875804",
        country="UK",
        insider_name="Jane",
        trade_type="Buy",
        trade_date=date(2025, 1, 2),
        source="rns",
    )
    service = FakeEuService([trade])
    monkeypatch.setattr(
        "insider_scanner.utils.config.load_eu_watchlist",
        lambda: ["GB0002875804", "GB0000000001"],
    )

    cli.cmd_eu_scan(
        Namespace(
            isin=None,
            watchlist=True,
            country="UK",
            type=None,
            min_value=None,
            since=date(2025, 1, 1),
            until=date(2025, 1, 31),
            save=False,
        ),
        _services(eu=service),
    )

    assert [call[0] for call in service.calls] == [
        "GB0002875804",
        "GB0000000001",
    ]
    assert all(call[1]["country"] == "UK" for call in service.calls)
    assert "Found 1 European insider trade(s)" in capsys.readouterr().out


def test_cli_default_us_scan_works_without_dates(tmp_path, capsys):
    persistence = open_persistence(tmp_path / "us-default.sqlite3")

    def latest(_count, _use_cache, _start, _end):
        return [
            InsiderTrade(
                ticker="AAPL",
                insider_name="Jane",
                trade_type="Buy",
                filing_date=date(2025, 1, 3),
                source="openinsider",
            )
        ]

    service = UsScanService(
        persistence,
        adapters={"secform4": lambda *_: [], "openinsider": lambda *_: []},
        latest_adapters={"openinsider": latest},
    )
    try:
        cli.cmd_scan(
            Namespace(
                ticker="aapl",
                since=None,
                until=None,
                no_cache=False,
                type=None,
                min_value=None,
                congress_only=False,
                save=False,
            ),
            _services(us=service),
        )
    finally:
        persistence.close()

    assert "Found 1 trades for AAPL" in capsys.readouterr().out


def test_cli_default_eu_scan_works_without_dates(tmp_path, capsys):
    persistence = open_persistence(tmp_path / "eu-default.sqlite3")

    def latest(_count, _use_cache, _start, _end):
        return [
            EuropeanInsiderTrade(
                isin="GB0002875804",
                country="UK",
                regulatory_body="FCA",
                insider_name="Jane",
                trade_type="Buy",
                trade_date=date(2025, 1, 2),
                source="rns",
            )
        ]

    service = EuropeanScanService(
        persistence,
        adapters={source: lambda *_: [] for source in ("rns", "bafin", "amf", "afm")},
        latest_adapters={"rns": latest},
    )
    try:
        cli.cmd_eu_scan(
            Namespace(
                isin="GB0002875804",
                watchlist=False,
                country="All",
                type=None,
                min_value=None,
                since=None,
                until=None,
                save=False,
            ),
            _services(eu=service),
        )
    finally:
        persistence.close()

    assert "Found 1 European insider trade(s)" in capsys.readouterr().out


def test_parser_registers_import_legacy_command(tmp_path):
    args = cli.build_parser().parse_args(["import-legacy", str(tmp_path)])
    assert args.command == "import-legacy"
    assert args.path == tmp_path
    assert args.max_file_size_mib == 50
    custom = cli.build_parser().parse_args(
        ["import-legacy", str(tmp_path), "--max-file-size-mib", "75"]
    )
    assert custom.max_file_size_mib == 75


def test_run_fails_closed_when_persistence_bootstrap_fails(capsys):
    def fail():
        raise RuntimeError("database unavailable")

    code = cli.run(["latest"], service_factory=fail)

    assert code != 0
    assert "Could not initialize local database." in capsys.readouterr().err


def test_run_reports_command_failure_without_calling_network(capsys):
    class FailingUsService:
        def latest(self, **kwargs):
            raise RuntimeError("internal database details")

    class Services:
        us = FailingUsService()

        def close(self):
            pass

    code = cli.run(["latest"], service_factory=Services)

    assert code != 0
    error = capsys.readouterr().err
    assert "Command failed. See logs for details." in error
    assert "internal database details" not in error


def test_import_legacy_exit_code_reports_clean_and_partial_success(tmp_path, capsys):
    valid = {
        "ticker": "AAPL",
        "insider_name": "Jane Doe",
        "trade_date": "2025-01-02",
        "source": "openinsider",
        "shares": 10,
    }

    class Services:
        def __init__(self, database):
            self.persistence = open_persistence(database)

        def close(self):
            self.persistence.close()

    clean_path = tmp_path / "clean.json"
    clean_path.write_text(json.dumps([valid]), encoding="utf-8")
    clean_code = cli.run(
        ["import-legacy", str(clean_path)],
        service_factory=lambda: Services(tmp_path / "clean.sqlite3"),
    )

    partial_path = tmp_path / "partial.json"
    partial_path.write_text(
        json.dumps([valid, {**valid, "ticker": "", "secret": "do-not-print"}]),
        encoding="utf-8",
    )
    partial_code = cli.run(
        ["import-legacy", str(partial_path)],
        service_factory=lambda: Services(tmp_path / "partial.sqlite3"),
    )

    output = capsys.readouterr()
    assert clean_code == 0
    assert partial_code != 0
    assert "inserted=1" in output.out
    assert "invalid_record" in output.out
    assert "do-not-print" not in output.out
    assert "do-not-print" not in output.err


def test_run_close_failure_changes_success_to_nonzero_and_is_concise(capsys, caplog):
    class Services:
        us = FakeUsService([])

        def close(self):
            raise RuntimeError("database-path=secret")

    code = cli.run(["latest"], service_factory=Services)

    assert code != 0
    error = capsys.readouterr().err
    assert "Could not close local database cleanly." in error
    assert "database-path=secret" not in error
    assert "CLI service shutdown failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "database-path=secret" not in caplog.text


def test_run_close_failure_does_not_override_prior_nonzero(monkeypatch, capsys):
    command = Mock(return_value=7)
    parser = Mock()
    parser.parse_args.return_value = Namespace(func=command)
    monkeypatch.setattr(cli, "build_parser", lambda: parser)

    class Services:
        def close(self):
            raise RuntimeError("database-path=secret")

    code = cli.run([], service_factory=Services)

    assert code == 7
    assert "Could not close local database cleanly." in capsys.readouterr().err


def test_import_legacy_oversized_file_returns_nonzero_without_reading(tmp_path, capsys):
    path = tmp_path / "oversized.json"
    with path.open("wb") as stream:
        stream.truncate(DEFAULT_MAX_LEGACY_FILE_SIZE_BYTES + 1)

    class Services:
        def __init__(self):
            self.persistence = open_persistence(tmp_path / "db.sqlite3")

        def close(self):
            self.persistence.close()

    code = cli.run(["import-legacy", str(path)], service_factory=Services)

    output = capsys.readouterr()
    assert code != 0
    assert "file_too_large" in output.out
    assert "oversized" not in output.err
