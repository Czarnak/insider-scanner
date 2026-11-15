"""Backfill progress and failure diagnostics remain useful across resumes."""

from __future__ import annotations

import json
import zipfile

import pytest

from insider_scanner.core.sec_client import SecHttpError
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ingestion import new_counters, process_filing_row
from insider_scanner.services.sec_backfill import SecBackfillService
from tests.sec_stubs import DAY, VALID_FILING, make_client, make_transport


def _archive(path, groups):
    with zipfile.ZipFile(path, "w") as archive:
        for index, accessions in enumerate(groups, 1):
            archive.writestr(
                f"CIK{index:010}.json",
                json.dumps(
                    {
                        "cik": str(index),
                        "filings": {
                            "recent": {
                                "accessionNumber": accessions,
                                "filingDate": ["2026-06-15"] * len(accessions),
                                "form": ["4"] * len(accessions),
                            }
                        },
                    }
                ),
            )
    return path


def _service(persistence, tmp_path, **kwargs):
    return SecBackfillService(
        persistence,
        client=make_client(make_transport(filing_bytes=VALID_FILING)),
        cache_root=tmp_path / "cache",
        **kwargs,
    )


def test_backfill_reports_before_first_large_batch_commits(
    persistence, tmp_path, monkeypatch
):
    archive = _archive(
        tmp_path / "source.zip",
        [[f"0000000001-26-{index:06}" for index in range(1, 151)]],
    )
    monkeypatch.setattr("insider_scanner.services.sec_backfill.monotonic", lambda: 0.0)
    snapshots = []

    def progress(snapshot):
        assert snapshot.transactions_inserted == len(
            persistence.us_trades.query_latest(500)
        )
        snapshots.append(snapshot)

    summary = _service(persistence, tmp_path).run(
        archive, confirm=True, on_progress=progress
    )
    assert any(0 < item.filings_parsed < 150 for item in snapshots)
    assert summary.members_completed == summary.members_total == 1
    assert summary.eta_seconds == 0.0
    assert summary.filings_committed == summary.filings_parsed == 150


def test_elapsed_progress_is_emitted_during_slow_small_batch(
    persistence, tmp_path, monkeypatch
):
    archive = _archive(
        tmp_path / "source.zip",
        [[f"0000000001-26-{index:06}" for index in range(1, 4)]],
    )
    ticks = iter(range(0, 100, 3))
    monkeypatch.setattr(
        "insider_scanner.services.sec_backfill.monotonic", lambda: float(next(ticks))
    )
    snapshots = []
    summary = _service(persistence, tmp_path).run(
        archive, confirm=True, on_progress=snapshots.append
    )
    assert any(0 < item.filings_parsed < 3 for item in snapshots)
    assert summary.elapsed_seconds > 0
    assert summary.filings_per_second > 0


def test_cumulative_archive_progress_survives_resume(persistence, tmp_path):
    archive = _archive(
        tmp_path / "source.zip",
        [["0000000001-26-000001"], ["0000000002-26-000001"]],
    )
    fetched = []
    service = SecBackfillService(
        persistence,
        client=make_client(make_transport(filing_bytes=VALID_FILING, fetched=fetched)),
        cache_root=tmp_path / "cache",
        batch_filings=1,
    )
    first = service.run(archive, confirm=True, cancelled=lambda: len(fetched) == 1)
    assert first.cancelled
    assert first.members_completed == 1
    assert first.eta_seconds is not None and first.eta_seconds > 0
    snapshots = []
    second = _service(persistence, tmp_path).run(
        archive, confirm=True, on_progress=snapshots.append
    )
    assert second.filings_parsed == 1
    assert second.members_completed == second.members_total == 2


def test_empty_members_emit_bounded_progress(persistence, tmp_path, monkeypatch):
    archive = _archive(tmp_path / "source.zip", [[] for _ in range(301)])
    monkeypatch.setattr("insider_scanner.services.sec_backfill.monotonic", lambda: 0.0)
    snapshots = []
    summary = _service(persistence, tmp_path).run(
        archive, confirm=True, on_progress=snapshots.append
    )
    assert len(snapshots) >= 3
    assert summary.members_completed == summary.members_total == 301


def test_empty_member_discovery_cancels_and_closes_archive(
    persistence, tmp_path, monkeypatch
):
    archive = _archive(tmp_path / "source.zip", [[] for _ in range(10)])
    opened = []
    original = zipfile.ZipFile.open

    def recording(self, info, *args, **kwargs):
        opened.append(info.filename)
        return original(self, info, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", recording)
    summary = _service(persistence, tmp_path).run(
        archive, confirm=True, cancelled=lambda: len(opened) >= 3
    )
    assert summary.cancelled
    assert summary.members_completed == len(opened) == 3
    assert summary.members_total == 10
    archive.rename(tmp_path / "closed.zip")


def test_http_failure_logs_accession_status_and_sanitized_url(
    tmp_path, monkeypatch, caplog
):
    accession = "0000000001-26-000001"
    url = f"https://private:secret@www.sec.gov/Archives/{accession}.txt?token=secret#hidden"

    def fail(*args, **kwargs):
        raise SecHttpError(503, url)

    monkeypatch.setattr("insider_scanner.core.sec_ingestion.fetch_filing", fail)
    outcome = process_filing_row(
        SecMasterIndexRow("1", "TEST", "4", DAY, f"edgar/data/1/{accession}.txt"),
        client=make_client(make_transport()),
        cache_root=tmp_path,
        counters=new_counters(),
    )
    assert f"accession={accession}" in caplog.text
    assert "http_status=503" in caplog.text
    assert f"url=https://www.sec.gov/Archives/{accession}.txt" in caplog.text
    assert "secret" not in caplog.text
    assert "token=" not in caplog.text
    assert "http_status=503" in outcome.reason
    assert "secret" not in outcome.reason


def test_http_failure_context_is_durable(persistence, tmp_path, monkeypatch):
    accession = "0000000001-26-000001"
    archive = _archive(tmp_path / "source.zip", [[accession]])

    def fail(*args, **kwargs):
        raise SecHttpError(
            503, f"https://www.sec.gov/Archives/{accession}.txt?token=secret"
        )

    monkeypatch.setattr("insider_scanner.core.sec_ingestion.fetch_filing", fail)
    summary = _service(persistence, tmp_path).run(archive, confirm=True)
    assert summary.failures == 1
    assert summary.members_completed == 0
    assert summary.eta_seconds is None
    reason = persistence.sec_ingestion.get(accession).reason
    assert "http_status=503" in reason
    assert accession in reason
    assert "secret" not in reason


@pytest.mark.parametrize("stage", ["parse", "persist"])
def test_non_http_failure_logs_identify_accession(tmp_path, monkeypatch, caplog, stage):
    from insider_scanner.core.sec_ownership_parser import SecOwnershipParseError
    from insider_scanner.persistence.errors import PersistenceError

    accession = "0000000001-26-000001"

    def fail(*args, **kwargs):
        if stage == "parse":
            raise SecOwnershipParseError("bad data")
        raise PersistenceError("database unavailable")

    if stage == "parse":
        monkeypatch.setattr(
            "insider_scanner.core.sec_ingestion.parse_ownership_document", fail
        )
    process_filing_row(
        SecMasterIndexRow("1", "TEST", "4", DAY, f"edgar/data/1/{accession}.txt"),
        client=make_client(make_transport()),
        cache_root=tmp_path,
        counters=new_counters(),
        on_filing_parsed=fail if stage == "persist" else None,
    )
    assert f"stage={stage}" in caplog.text
    assert f"accession={accession}" in caplog.text
