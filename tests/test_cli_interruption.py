"""SEC commands expose cooperative interruption and incomplete runs to scripts."""

import signal
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from insider_scanner import cli
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.services.sec_backfill import SecBackfillSummary
from insider_scanner.services.sec_daily import SecDailyIngestionSummary


def _daily_summary(**kwargs):
    values = dict(
        interval=DateInterval(date(2026, 1, 7), date(2026, 1, 7)),
        dates_total=1,
        dates_completed=1,
        filings_discovered=1,
        filings_parsed=1,
        transactions_inserted=1,
        transactions_updated=0,
        transactions_skipped=0,
        failures=0,
    )
    values.update(kwargs)
    return SecDailyIngestionSummary(**values)


@pytest.mark.parametrize("command", ["sec-daily", "sec-catchup"])
@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, 0),
        ({"failures": 1}, 0),
        ({"dates_completed": 0}, 3),
        ({"cancelled": True}, 130),
    ],
)
def test_daily_exit_status(command, overrides, expected, monkeypatch):
    client = Mock()
    monkeypatch.setattr(cli, "_build_sec_client", lambda: client)
    service = Mock()
    service.ingest_date.return_value = service.ingest_range.return_value = (
        _daily_summary(**overrides)
    )
    services = Mock()
    services.make_sec_daily.return_value = service
    argv = [command, "--quiet"]
    argv += (
        ["--date", "2026-01-07"]
        if command == "sec-daily"
        else ["--since", "2026-01-07", "--until", "2026-01-07"]
    )
    args = cli.build_parser().parse_args(argv)
    assert args.func(args, services) == expected
    client.close.assert_called_once_with()


def test_sigint_requests_stop_prints_summary_and_restores_handler(monkeypatch, capsys):
    previous = signal.getsignal(signal.SIGINT)
    client = Mock()
    monkeypatch.setattr(cli, "_build_sec_client", lambda: client)

    def ingest(day, *, cancelled, on_progress):
        assert not cancelled()
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert cancelled()
        return _daily_summary(cancelled=True, dates_completed=0)

    services = SimpleNamespace(
        make_sec_daily=lambda **k: SimpleNamespace(ingest_date=ingest)
    )
    args = cli.build_parser().parse_args(["sec-daily", "--date", "2026-01-07"])
    assert args.func(args, services) == 130
    assert signal.getsignal(signal.SIGINT) is previous
    output = capsys.readouterr()
    assert "SEC ingestion" in output.out
    assert "Interrupt" in output.err or "Stopping" in output.err
    client.close.assert_called_once_with()


def test_backfill_gets_progress_and_cancellation_hooks(monkeypatch, tmp_path, capsys):
    archive = tmp_path / "submissions.zip"
    archive.touch()
    monkeypatch.setattr(cli, "_build_sec_client", Mock)

    def backfill(path, *, confirm, ciks, cancelled, on_progress):
        assert callable(cancelled)
        assert callable(on_progress)
        summary = SecBackfillSummary(10, 5, 5, 0, 0, 0, 0, 0, cancelled=True)
        on_progress(summary)
        return summary

    services = SimpleNamespace(
        make_sec_backfill=lambda **k: SimpleNamespace(run=backfill)
    )
    args = cli.build_parser().parse_args(
        ["sec-backfill", "--zip", str(archive), "--confirm-full-backfill"]
    )
    assert args.func(args, services) == 130
    output = capsys.readouterr()
    assert "discovered=10" in output.err
    assert "rate=" in output.err
    assert "SEC backfill" in output.out


def test_backfill_quiet_suppresses_only_progress(monkeypatch, tmp_path, capsys):
    archive = tmp_path / "submissions.zip"
    archive.touch()
    monkeypatch.setattr(cli, "_build_sec_client", Mock)
    service = Mock()
    service.run.return_value = SecBackfillSummary(0, 0, 0, 0, 0, 0, 0, 0)
    services = SimpleNamespace(make_sec_backfill=lambda **k: service)
    args = cli.build_parser().parse_args(
        ["sec-backfill", "--zip", str(archive), "--confirm-full-backfill", "--quiet"]
    )
    assert args.func(args, services) == 0
    assert service.run.call_args.kwargs["on_progress"] is None
    assert "SEC backfill" in capsys.readouterr().out


def test_keyboard_interrupt_during_startup_returns_130(monkeypatch, capsys):
    monkeypatch.setattr(cli, "ensure_dirs", Mock(side_effect=KeyboardInterrupt))
    assert cli.run(["latest"], service_factory=Mock()) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_second_sigint_forces_interrupt_and_restores_handler(capsys):
    previous = signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        with cli._cooperative_interrupt() as cancelled:
            handler = signal.getsignal(signal.SIGINT)
            handler(signal.SIGINT, None)
            assert cancelled()
            handler(signal.SIGINT, None)
    assert signal.getsignal(signal.SIGINT) is previous


def test_backfill_progress_is_throttled_and_reports_committed_counts(
    monkeypatch, capsys
):
    ticks = iter((1.0, 1.1, 3.0))
    monkeypatch.setattr(cli, "monotonic", lambda: next(ticks))
    report = cli._make_backfill_progress(False)
    summary = SecBackfillSummary(10, 8, 5, 0, 0, 0, 0, 0, filings_committed=5)
    report(summary)
    report(summary)
    report(summary)
    output = capsys.readouterr()
    assert output.out == ""
    assert len(output.err.splitlines()) == 2
    assert "parsed=8 committed=5" in output.err


@pytest.mark.parametrize("skipped_members,failures,expected", [(1, 0, 3), (0, 1, 0)])
def test_backfill_member_failure_is_distinct_from_filing_failure(
    skipped_members, failures, expected, monkeypatch, tmp_path, capsys
):
    archive = tmp_path / "submissions.zip"
    archive.touch()
    monkeypatch.setattr(cli, "_build_sec_client", Mock)
    service = Mock()
    service.run.return_value = SecBackfillSummary(
        0, 0, 0, 0, 0, 0, 0, failures, skipped_members=skipped_members
    )
    services = SimpleNamespace(make_sec_backfill=lambda **k: service)
    args = cli.build_parser().parse_args(
        ["sec-backfill", "--zip", str(archive), "--confirm-full-backfill", "--quiet"]
    )
    assert args.func(args, services) == expected
    if skipped_members:
        assert "archive members" in capsys.readouterr().err
