"""Quarterly imports share durable filing commits and recoverable progress."""

from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import date

import pytest

from insider_scanner.persistence.errors import PersistenceError


def _service(persistence, **kwargs):
    name = "insider_scanner.services.sec_dataset"
    assert importlib.util.find_spec(name) is not None, (
        "dataset ingestion service is absent"
    )
    return importlib.import_module(name).SecDatasetService(persistence, **kwargs)


def test_quarterly_service_commits_shared_mapping_and_resumes(persistence, tmp_path):
    from tests.dataset_fixtures import make_dataset

    path = make_dataset(tmp_path / "quarter.zip")
    service = _service(persistence)
    first = service.run(path)
    assert first.filings_committed == first.transactions_inserted == 1
    (trade,) = persistence.us_trades.query_latest(10)
    assert trade.value == 25
    assert trade.edgar_url == trade.document_sha256 == ""
    assert json.loads(trade.sec_detail_json)["dataset_representation"] is True
    assert json.loads(trade.sec_detail_json)["narrative_complete"] is True
    second = service.run(path)
    assert second.skipped_resume == 1
    assert second.filings_committed == 0


def test_service_failed_commit_stays_retryable(persistence, tmp_path, monkeypatch):
    from tests.dataset_fixtures import ACCESSION, make_dataset

    path = make_dataset(tmp_path / "quarter.zip")
    original = persistence.sec_ingestion.commit

    def fail(entries):
        raise PersistenceError("database unavailable")

    monkeypatch.setattr(persistence.sec_ingestion, "commit", fail)
    failed = _service(persistence).run(path)
    assert failed.failures == 1
    assert failed.filings_committed == 0
    assert persistence.sec_ingestion.get(ACCESSION) is None
    monkeypatch.setattr(persistence.sec_ingestion, "commit", original)
    assert _service(persistence).run(path).filings_committed == 1


def test_service_cancellation_flushes_only_parsed_filings(persistence, tmp_path):
    from tests.dataset_fixtures import (
        ACCESSION,
        SECOND_ACCESSION,
        make_dataset,
        owner,
        submission,
        transaction,
    )

    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "SUBMISSION": [submission(), submission(SECOND_ACCESSION)],
            "REPORTINGOWNER": [owner(), owner(SECOND_ACCESSION)],
            "NONDERIV_TRANS": [transaction(), transaction(SECOND_ACCESSION)],
        },
    )
    snapshots = []

    def progress(snapshot):
        snapshots.append(snapshot)

    first = _service(persistence, batch_filings=1).run(
        path,
        cancelled=lambda: persistence.sec_ingestion.is_terminal(ACCESSION),
        on_progress=progress,
    )
    assert first.cancelled and first.filings_committed == 1
    assert any(item.rows_staged > 0 for item in snapshots)
    assert snapshots[-1] == first
    assert not persistence.sec_ingestion.is_terminal(SECOND_ACCESSION)
    assert _service(persistence).run(path).filings_committed == 1


def test_corrupt_filing_does_not_publish_success_or_hide_other_filings(
    persistence, tmp_path
):
    from tests.dataset_fixtures import (
        ACCESSION,
        SECOND_ACCESSION,
        make_dataset,
        owner,
        submission,
        transaction,
    )

    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "SUBMISSION": [submission(), submission(SECOND_ACCESSION)],
            "REPORTINGOWNER": [owner(), owner(SECOND_ACCESSION)],
            "NONDERIV_TRANS": [
                transaction(TRANS_SHARES="NaN"),
                transaction(SECOND_ACCESSION),
            ],
        },
    )
    result = _service(persistence).run(path)
    assert result.failures == result.filings_committed == 1
    assert not persistence.sec_ingestion.is_terminal(ACCESSION)
    assert persistence.sec_ingestion.is_terminal(SECOND_ACCESSION)


def test_service_date_bounds_do_not_mark_excluded_filings(persistence, tmp_path):
    from tests.dataset_fixtures import ACCESSION, make_dataset

    path = make_dataset(tmp_path / "quarter.zip")
    result = _service(persistence).run(path, since=date(2026, 7, 1))
    assert result.filings_discovered == 0
    assert persistence.sec_ingestion.get(ACCESSION) is None


def test_service_reimport_updates_dataset_without_degrading_xml(persistence, tmp_path):
    from tests.dataset_fixtures import make_dataset, transaction

    path = make_dataset(tmp_path / "quarter.zip")
    service = _service(persistence)
    service.run(path)
    make_dataset(path, tables={"NONDERIV_TRANS": [transaction(TRANS_SHARES="20")]})
    assert service.run(path).skipped_resume == 1
    refreshed = service.run(path, reimport=True)
    assert refreshed.transactions_updated == 1
    assert persistence.us_trades.query_latest(10)[0].value == 40


@pytest.mark.parametrize("capped", [False, True])
def test_dataset_amendments_use_complete_uncapped_narrative(
    persistence, tmp_path, capped
):
    from tests.dataset_fixtures import (
        SECOND_ACCESSION,
        make_dataset,
        owner,
        submission,
        transaction,
    )

    narrative = "Corrects the transaction shares."
    if capped:
        narrative += " " * (2000 - len(narrative) - 1) + "."
    path = make_dataset(
        tmp_path / "amendment.zip",
        tables={
            "SUBMISSION": [
                submission(),
                submission(
                    SECOND_ACCESSION,
                    DOCUMENT_TYPE="4/A",
                    FILING_DATE="16-JUN-2026",
                    DATE_OF_ORIG_SUB="15-JUN-2026",
                    REMARKS=narrative,
                ),
            ],
            "REPORTINGOWNER": [owner(), owner(SECOND_ACCESSION)],
            "NONDERIV_TRANS": [
                transaction(),
                transaction(SECOND_ACCESSION, TRANS_SHARES="20"),
            ],
        },
    )
    assert _service(persistence).run(path).filings_committed == 2
    assert len(persistence.us_trades.query_raw("EXMP")) == 2
    visible = persistence.us_trades.query_latest(10)
    assert len(visible) == (2 if capped else 1)
    if not capped:
        assert visible[0].value == 40


def test_missing_reference_columns_cannot_prove_amendment_narrative(
    persistence, tmp_path
):
    from tests.dataset_fixtures import (
        COLUMNS,
        SECOND_ACCESSION,
        make_dataset,
        owner,
        submission,
        transaction,
    )

    path = make_dataset(
        tmp_path / "incomplete.zip",
        tables={
            "SUBMISSION": [
                submission(),
                submission(
                    SECOND_ACCESSION,
                    DOCUMENT_TYPE="4/A",
                    FILING_DATE="16-JUN-2026",
                    DATE_OF_ORIG_SUB="15-JUN-2026",
                ),
            ],
            "REPORTINGOWNER": [owner(), owner(SECOND_ACCESSION)],
            "NONDERIV_TRANS": [transaction(), transaction(SECOND_ACCESSION)],
            "FOOTNOTES": [
                {
                    "ACCESSION_NUMBER": SECOND_ACCESSION,
                    "FOOTNOTE_ID": "F1",
                    "FOOTNOTE_TXT": "Additional omitted transaction",
                }
            ],
        },
        columns={
            table: tuple(name for name in COLUMNS[table] if name != "SECURITY_TITLE_FN")
            for table in ("NONDERIV_TRANS", "DERIV_TRANS")
        },
    )
    assert _service(persistence).run(path).filings_committed == 2
    assert len(persistence.us_trades.query_latest(10)) == 2
