"""CLI input errors and script-visible outcomes."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from insider_scanner import cli


@pytest.mark.parametrize(
    "argv",
    [
        ["latest", "--count", "0"],
        ["latest", "--count", "-1"],
        ["scan", "AAPL", "--source", "unknown"],
        ["latest", "--source", "unknown"],
    ],
)
def test_invalid_count_and_source_are_usage_errors(argv):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(argv)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["scan", "AAPL", "--since", "2026-01-01"],
        ["latest", "--until", "2026-01-01"],
        ["scan", "AAPL", "--since", "2026-02-01", "--until", "2026-01-01"],
        ["price", "AAPL", "--since", "2026-02-01", "--until", "2026-01-01"],
    ],
)
def test_invalid_ranges_fail_before_startup(argv, monkeypatch, capsys):
    startup = Mock(side_effect=AssertionError("Invalid input reached startup"))
    monkeypatch.setattr(cli, "ensure_dirs", startup)
    assert cli.run(argv, service_factory=startup) == 2
    startup.assert_not_called()
    assert "Invalid" in capsys.readouterr().err


def test_unresolved_cik_is_failure_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr("insider_scanner.core.edgar.resolve_cik", lambda *a, **k: None)
    args = cli.build_parser().parse_args(["resolve-cik", "UNKNOWN"])
    assert cli.cmd_resolve_cik(args, None) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Could not resolve CIK for UNKNOWN" in output.err


def test_resolved_cik_is_success(monkeypatch, capsys):
    monkeypatch.setattr(
        "insider_scanner.core.edgar.resolve_cik", lambda *a, **k: "320193"
    )
    args = cli.build_parser().parse_args(["cik", "aapl"])
    assert cli.cmd_resolve_cik(args, None) == 0
    assert "AAPL" in capsys.readouterr().out


def test_latest_reports_saved_path(monkeypatch, capsys, tmp_path):
    destination = tmp_path / "latest_scan.json"
    monkeypatch.setattr(
        "insider_scanner.core.merger.save_scan_results", lambda *a, **k: destination
    )
    services = SimpleNamespace(us=SimpleNamespace(latest=lambda **k: []))
    cli.cmd_latest(cli.build_parser().parse_args(["latest", "--save"]), services)
    assert str(destination) in capsys.readouterr().out


def test_command_value_error_is_usage_error_and_closes_services(monkeypatch, capsys):
    services = Mock()
    services.us.latest.side_effect = ValueError("count must be positive")
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    assert cli.run(["latest"], service_factory=lambda: services) == 2
    services.close.assert_called_once_with()
    output = capsys.readouterr()
    assert "count must be positive" in output.err
    assert "Traceback" not in output.err


def test_price_single_bound_keeps_existing_default(monkeypatch):
    history = Mock(return_value=[])
    services = SimpleNamespace(prices=SimpleNamespace(get_history=history))
    args = cli.build_parser().parse_args(["price", "AAPL", "--until", "2026-01-01"])
    cli.cmd_price(args, services)
    assert history.call_args.args[1] < history.call_args.args[2]
