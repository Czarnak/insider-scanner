"""The dataset CLI validates early and makes publication gaps explicit."""

from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest

from insider_scanner import cli
from insider_scanner.core.sec_bulk import SecBulkError
from insider_scanner.core.sec_dataset_downloads import DatasetQuarter
from insider_scanner.services.sec_dataset import SecDatasetService
from tests.dataset_fixtures import make_dataset
from tests.sec_stubs import StubResponse, make_client


def _args(*words):
    parser = cli.build_parser()
    assert "sec-backfill-dataset" in parser.format_help(), (
        "quarterly dataset CLI is absent"
    )
    return parser.parse_args(["sec-backfill-dataset", *words])


class Services:
    def __init__(self, persistence):
        self.persistence = persistence

    def make_sec_dataset(self, **kwargs):
        return SecDatasetService(self.persistence, **kwargs)


def test_local_dataset_cli_needs_no_sec_network_identity(
    persistence, tmp_path, monkeypatch, capsys
):
    path = make_dataset(tmp_path / "quarter.zip")
    monkeypatch.setattr(
        cli,
        "_build_sec_client",
        lambda: pytest.fail("local import made network client"),
    )
    args = _args("--zip", str(path), "--quiet")
    assert args.func(args, Services(persistence)) == 0
    output = capsys.readouterr()
    assert "committed=1" in output.out
    assert "staged=" not in output.err
    assert len(persistence.us_trades.query_latest(10)) == 1


@pytest.mark.parametrize(
    "words",
    [
        ["--since", "2026-06-16", "--until", "2026-06-15"],
        ["--since", "2026-01-01", "--until", "2099-01-01"],
        ["--cik", "not-a-cik"],
        ["--zip", "missing.zip"],
    ],
)
def test_dataset_cli_invalid_input_fails_before_network(
    persistence, monkeypatch, words, capsys
):
    monkeypatch.setattr(
        cli, "_build_sec_client", lambda: pytest.fail("invalid input reached network")
    )
    args = _args(*words)
    assert args.func(args, Services(persistence)) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_automatic_dataset_cli_downloads_one_archive_not_filings(
    persistence, tmp_path, monkeypatch
):
    archive = make_dataset(tmp_path / "fixture.zip").read_bytes()
    url = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2026q2_form345.zip"
    requests = []

    def get(url, **kwargs):
        requests.append(url)
        return StubResponse(
            chunks=(archive,)
            if url.endswith(".zip")
            else (f'<a href="{url_quarter}">Quarter</a>'.encode(),),
            headers={
                "Content-Type": "application/zip"
                if url.endswith(".zip")
                else "text/html"
            },
        )

    url_quarter = url
    transport = Mock()
    transport.get.side_effect = get
    client = make_client(transport)
    monkeypatch.setattr(cli, "_build_sec_client", lambda: client)
    args = _args(
        "--since",
        "2026-04-01",
        "--until",
        "2026-06-15",
        "--download-dir",
        str(tmp_path / "downloads"),
    )
    assert args.func(args, Services(persistence)) == 0
    assert len(requests) == 2
    assert requests[-1] == url


def test_missing_published_quarters_and_early_history_return_partial(
    persistence, tmp_path, monkeypatch, capsys
):
    import insider_scanner.cli_sec_dataset as downloads

    quarter = DatasetQuarter(2026, 2, "https://www.sec.gov/files/2026q2_form345.zip")
    monkeypatch.setattr(
        downloads, "discover_dataset_quarters", lambda client: (quarter,)
    )
    monkeypatch.setattr(cli, "_build_sec_client", lambda: make_client(Mock()))
    monkeypatch.setattr(
        downloads,
        "download_dataset_quarter",
        lambda client, quarter, destination, **kwargs: make_dataset(destination),
    )
    directory = tmp_path / "downloads"
    directory.mkdir()
    args = _args(
        "--since",
        "2005-12-30",
        "--until",
        "2026-06-30",
        "--download-dir",
        str(directory),
    )
    assert args.func(args, Services(persistence)) == 3
    error = capsys.readouterr().err
    assert "2003" in error and "2005" in error
    assert "sec-catchup" in error and "quarter" in error.lower()


def test_local_malformed_filing_reports_failure_exit(persistence, tmp_path, capsys):
    from tests.dataset_fixtures import transaction

    path = make_dataset(
        tmp_path / "bad.zip",
        tables={"NONDERIV_TRANS": [transaction(TRANS_PRICEPERSHARE="NaN")]},
    )
    args = _args("--zip", str(path))
    assert args.func(args, Services(persistence)) == 3
    assert "failures=1" in capsys.readouterr().out


def test_backfill_parser_exposes_date_bounds_and_scoped_force(tmp_path):
    args = cli.build_parser().parse_args(
        [
            "sec-backfill",
            "--zip",
            "submissions.zip",
            "--since",
            "2003-01-01",
            "--until",
            "2005-12-31",
            "--cik",
            "1",
            "--force",
            "--confirm-full-backfill",
        ]
    )
    assert args.since == date(2003, 1, 1)
    assert args.until == date(2005, 12, 31)
    assert args.force


def test_quarter_end_holiday_cutoff_includes_following_posting():
    from insider_scanner.cli_sec_dataset import _requested_quarters

    # EDGAR was closed on December 31, 2021; the final filing day was Thursday.
    assert _requested_quarters(date(2021, 12, 30), date(2021, 12, 30)) == {
        (2021, 4),
        (2022, 1),
    }


def test_dataset_archive_error_explains_repairable_reason(
    persistence, tmp_path, monkeypatch, capsys
):
    service = SecDatasetService(persistence)
    monkeypatch.setattr(
        service,
        "run",
        Mock(side_effect=SecBulkError("Dataset is missing a required table")),
    )
    services = Services(persistence)
    monkeypatch.setattr(services, "make_sec_dataset", lambda: service)
    path = make_dataset(tmp_path / "quarter.zip")
    args = _args("--zip", str(path))
    assert args.func(args, services) == 1
    assert "missing a required table" in capsys.readouterr().err


def test_dataset_cli_cooperative_stop_returns_interrupted(
    persistence, tmp_path, monkeypatch
):
    from contextlib import contextmanager

    @contextmanager
    def stopped():
        yield lambda: True

    monkeypatch.setattr(cli, "_cooperative_interrupt", stopped)
    path = make_dataset(tmp_path / "quarter.zip")
    args = _args("--zip", str(path))
    assert args.func(args, Services(persistence)) == 130
    assert persistence.us_trades.query_latest(10) == []
