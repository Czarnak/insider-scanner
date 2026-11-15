"""Date-bounded XML repairs remain independent of full archive progress."""

import json
import zipfile
from datetime import date

import pytest

from insider_scanner.services.sec_backfill import SecBackfillService
from tests.sec_stubs import VALID_FILING, make_client, make_transport


def _archive(tmp_path):
    path = tmp_path / "submissions.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "CIK0000000001.json",
            json.dumps(
                {
                    "cik": "1",
                    "filings": {
                        "recent": {
                            "accessionNumber": [
                                "0000000001-26-000001",
                                "0000000001-26-000002",
                            ],
                            "filingDate": ["2026-06-15", "2026-06-16"],
                            "form": ["4", "4"],
                        }
                    },
                }
            ),
        )
    return path


def _service(persistence, tmp_path, fetched):
    return SecBackfillService(
        persistence,
        client=make_client(make_transport(filing_bytes=VALID_FILING, fetched=fetched)),
        cache_root=tmp_path / "cache",
    )


def test_xml_filing_dates_filter_before_fetch_and_do_not_poison_other_scopes(
    persistence, tmp_path
):
    path = _archive(tmp_path)
    fetched = []
    service = _service(persistence, tmp_path, fetched)
    service.run(path, confirm=True, since=date(2026, 6, 16), until=date(2026, 6, 16))
    assert len(fetched) == 1 and "000002.txt" in fetched[0]
    service.run(path, confirm=True, since=date(2026, 6, 15), until=date(2026, 6, 15))
    assert len(fetched) == 2 and "000001.txt" in fetched[-1]


def test_force_xml_repair_bypasses_terminal_ledger_and_archive_cursor(
    persistence, tmp_path
):
    path = _archive(tmp_path)
    fetched = []
    service = _service(persistence, tmp_path, fetched)
    service.run(path, confirm=True, ciks=frozenset({"0000000001"}))
    assert len(fetched) == 2
    service.run(path, confirm=True, ciks=frozenset({"0000000001"}), force=True)
    assert len(fetched) == 4
    service.run(path, confirm=True, ciks=frozenset({"0000000001"}), force=True)
    assert len(fetched) == 6
    assert len(persistence.us_trades.query_latest(10)) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"since": date(2026, 6, 16), "until": date(2026, 6, 15)},
        {"force": True},
    ],
)
def test_invalid_repair_scope_fails_before_archive_and_network(
    persistence, tmp_path, kwargs
):
    fetched = []
    with pytest.raises(ValueError):
        _service(persistence, tmp_path, fetched).run(
            tmp_path / "absent.zip", confirm=True, **kwargs
        )
    assert fetched == []


def test_targeted_xml_repair_replaces_dataset_rows(persistence, tmp_path):
    from insider_scanner.services.sec_dataset import SecDatasetService
    from tests.dataset_fixtures import make_dataset

    SecDatasetService(persistence).run(make_dataset(tmp_path / "quarter.zip"))
    fetched = []
    result = _service(persistence, tmp_path, fetched).run(
        _archive(tmp_path),
        confirm=True,
        since=date(2026, 6, 15),
        until=date(2026, 6, 15),
        force=True,
    )
    assert result.filings_committed == 1
    (trade,) = persistence.us_trades.query_latest(10)
    assert ":dataset:" not in trade.sec_row_id
    assert trade.edgar_url
