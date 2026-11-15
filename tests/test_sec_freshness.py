"""SEC dates stay retryable until the filing calendar day has ended."""

from argparse import Namespace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from insider_scanner import cli
from insider_scanner.persistence.json_state import atomic_write_json
from insider_scanner.services import sec_downloads
from insider_scanner.services.sec_daily import (
    SEC_DAILY_IDENTIFIER,
    SecDailyIngestionService,
    SecDailyIngestionSummary,
)
from insider_scanner.services.sec_downloads import (
    SecFilingStatus,
    download_filings_in_range,
)
from tests.sec_stubs import DAY, EMPTY_INDEX, INDEX_ONE_ROW, make_client, make_transport


def test_missing_current_index_is_retried_after_publication(persistence, tmp_path):
    today = DAY
    transport = make_transport(index_status=404)
    snapshots = []
    service = SecDailyIngestionService(
        persistence,
        client=make_client(transport),
        cache_root=tmp_path / "cache",
        clock=lambda: today,
    )

    first = service.ingest_date(DAY, on_progress=snapshots.append)

    assert first.dates_completed == 0
    assert first.outcomes[0].status is SecFilingStatus.RETRYABLE_FAILURE
    assert first.outcomes[0].stage == "index"
    assert snapshots == []
    assert persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar") == ()

    today += timedelta(days=1)
    transport.get.side_effect = make_transport(index_text=INDEX_ONE_ROW).get.side_effect
    second = service.ingest_date(DAY, on_progress=snapshots.append)

    assert second.dates_completed == 1
    assert second.transactions_inserted == 1
    assert [snapshot.current_date for snapshot in snapshots] == [DAY]
    assert persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar")


def test_available_current_index_persists_rows_but_stays_uncovered(
    persistence, tmp_path
):
    service = SecDailyIngestionService(
        persistence,
        client=make_client(make_transport(index_text=INDEX_ONE_ROW)),
        cache_root=tmp_path / "cache",
        clock=lambda: DAY,
    )

    summary = service.ingest_date(DAY)

    assert summary.transactions_inserted == 1
    assert summary.dates_completed == 0
    assert summary.outcomes[0].reason == "index_not_final"
    assert persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar") == ()


@pytest.mark.parametrize("version", [1, 2])
def test_current_index_ignores_legacy_checkpoint_file(tmp_path, version):
    checkpoint = tmp_path / "checkpoint.json"
    atomic_write_json(
        checkpoint,
        {
            "version": version,
            "range": {"start": DAY.isoformat(), "end": DAY.isoformat()},
            "completed_dates": [DAY.isoformat()],
        },
    )
    fetched = []

    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport(index_status=404, fetched=fetched)),
        cache_root=tmp_path / "cache",
        clock=lambda: DAY,
    )

    assert fetched
    assert report.dates_completed == 0
    assert checkpoint.exists()


def test_legacy_checkpoint_preserved_after_historic_download(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    atomic_write_json(
        checkpoint,
        {
            "version": 1,
            "range": {"start": DAY.isoformat(), "end": DAY.isoformat()},
            "completed_dates": [DAY.isoformat()],
            "counters": {"discovered": 99, "parsed": 99},
        },
    )
    fetched = []
    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport(index_text=INDEX_ONE_ROW, fetched=fetched)),
        cache_root=tmp_path / "cache",
        clock=lambda: DAY + timedelta(days=1),
    )

    assert any(url.endswith("master.20260615.idx") for url in fetched)
    assert report.dates_completed == 1
    assert report.discovered == report.parsed == 1
    assert checkpoint.exists()


def test_missing_elapsed_index_is_a_completed_empty_day(tmp_path):
    report = download_filings_in_range(
        start_date=DAY,
        end_date=DAY,
        client=make_client(make_transport(index_status=404)),
        cache_root=tmp_path / "cache",
        clock=lambda: DAY + timedelta(days=1),
    )

    assert report.dates_completed == 1
    assert report.outcomes == ()


@pytest.mark.parametrize("entry_point", ["download", "ingest"])
def test_future_sec_dates_are_rejected_without_requests(
    persistence, tmp_path, entry_point
):
    fetched = []
    client = make_client(make_transport(index_text=EMPTY_INDEX, fetched=fetched))
    future = DAY + timedelta(days=1)

    with pytest.raises(ValueError, match="future"):
        if entry_point == "download":
            download_filings_in_range(
                start_date=DAY,
                end_date=future,
                client=client,
                cache_root=tmp_path / "cache",
                clock=lambda: DAY,
            )
        else:
            SecDailyIngestionService(
                persistence,
                client=client,
                cache_root=tmp_path / "cache",
                clock=lambda: DAY,
            ).ingest_range(DAY, future)

    assert fetched == []
    assert persistence.coverage.get("us", SEC_DAILY_IDENTIFIER, "sec_edgar") == ()


def test_sec_calendar_uses_eastern_date_near_utc_midnight(monkeypatch):
    instant = datetime(2026, 11, 14, 2, tzinfo=UTC)
    monkeypatch.setattr(
        sec_downloads,
        "datetime",
        SimpleNamespace(now=lambda zone: instant.astimezone(zone)),
    )

    assert sec_downloads.sec_today() == date(2026, 11, 13)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 13), date(2026, 11, 12)),
        (date(2026, 11, 14), date(2026, 11, 13)),
        (date(2026, 11, 15), date(2026, 11, 13)),
        (date(2026, 11, 16), date(2026, 11, 13)),
    ],
)
def test_default_sec_daily_uses_previous_weekday(monkeypatch, today, expected):
    monkeypatch.setattr(cli, "sec_today", lambda: today)
    monkeypatch.setattr(cli, "_build_sec_client", Mock())
    ingest = Mock(
        return_value=SecDailyIngestionSummary(
            interval=sec_downloads.DateInterval(expected, expected),
            dates_total=1,
            dates_completed=1,
            filings_discovered=0,
            filings_parsed=0,
            transactions_inserted=0,
            transactions_updated=0,
            transactions_skipped=0,
            failures=0,
        )
    )
    services = SimpleNamespace(
        make_sec_daily=lambda **kwargs: SimpleNamespace(ingest_date=ingest)
    )

    assert (
        cli.cmd_sec_daily(Namespace(date=None, no_cleanup=False, quiet=True), services)
        == 0
    )
    assert ingest.call_args.args == (expected,)


@pytest.mark.parametrize("command", ["sec-daily", "sec-catchup"])
def test_cli_rejects_future_sec_dates_before_client_creation(
    monkeypatch, capsys, command
):
    monkeypatch.setattr(cli, "sec_today", lambda: DAY)
    build_client = Mock()
    monkeypatch.setattr(cli, "_build_sec_client", build_client)
    future = (DAY + timedelta(days=1)).isoformat()
    argv = (
        [command, "--date", future]
        if command == "sec-daily"
        else [command, "--since", DAY.isoformat(), "--until", future]
    )
    args = cli.build_parser().parse_args(argv)

    assert args.func(args, SimpleNamespace()) == 2
    assert "future" in capsys.readouterr().err
    build_client.assert_not_called()
