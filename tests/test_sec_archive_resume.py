"""Real ZIP resume skips completed members while preserving earlier failures."""

import json
import zipfile

from insider_scanner.persistence.errors import PersistenceError

from insider_scanner.core.sec_bulk import bulk_archive_fingerprint
from insider_scanner.core.sec_ingestion import SecFilingStatus
from insider_scanner.services.sec_backfill import SecBackfillService
from tests.sec_stubs import StubResponse, make_client, make_transport


def _archive(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for index, accessions in enumerate(members):
            payload = {
                "filings": {
                    "recent": {
                        "accessionNumber": accessions,
                        "form": ["4"] * len(accessions),
                        "filingDate": ["2026-06-15"] * len(accessions),
                    }
                }
            }
            archive.writestr(
                f"CIK{index + 1:010d}.json",
                json.dumps(payload) if accessions != ["bad"] else "{bad",
            )
    return path


def _service(persistence, tmp_path, transport=None, **kwargs):
    return SecBackfillService(
        persistence,
        client=make_client(transport or make_transport()),
        cache_root=tmp_path / "cache",
        **kwargs,
    )


def _track_opens(monkeypatch):
    opened = []
    original = zipfile.ZipFile.open

    def recording(self, info, *args, **kwargs):
        opened.append(info.filename)
        return original(self, info, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", recording)
    return opened


def test_resume_skips_completed_prefix_even_after_archive_rename(
    persistence, tmp_path, monkeypatch
):
    accessions = [f"0000000001-26-{index:06d}" for index in range(1, 4)]
    archive = _archive(tmp_path / "source.zip", [[item] for item in accessions])
    fetched = []
    _service(
        persistence, tmp_path, make_transport(fetched=fetched), batch_filings=1
    ).run(
        archive,
        confirm=True,
        cancelled=lambda: len(fetched) == 1,
    )
    renamed = archive.rename(tmp_path / "renamed.zip")
    opened = _track_opens(monkeypatch)
    summary = _service(persistence, tmp_path).run(renamed, confirm=True)
    assert opened == ["CIK0000000002.json", "CIK0000000003.json"]
    assert summary.filings_parsed == 2
    assert summary.skipped_resume == 0
    assert not (tmp_path / "cache").exists()


def test_retry_before_cursor_does_not_reopen_other_completed_members(
    persistence, tmp_path, monkeypatch
):
    first, second = "0000000001-26-000001", "0000000001-26-000002"
    archive = _archive(tmp_path / "source.zip", [[first], [second]])
    transport = make_transport()
    original = transport.get.side_effect
    transport.get.side_effect = lambda url, **kw: (
        StubResponse(status_code=503) if first in url else original(url, **kw)
    )
    summary = _service(persistence, tmp_path, transport).run(archive, confirm=True)
    assert summary.failures == 1
    position = persistence.sec_ingestion.archive_progress(
        bulk_archive_fingerprint(archive), "full"
    )
    assert position.next_member == 2
    assert position.retry_members == frozenset({0})
    opened = _track_opens(monkeypatch)
    summary = _service(persistence, tmp_path).run(archive, confirm=True)
    assert opened == ["CIK0000000001.json"]
    assert summary.filings_parsed == 1
    assert persistence.sec_ingestion.get(first).status is SecFilingStatus.SUCCESS
    assert not persistence.sec_ingestion.archive_progress(
        bulk_archive_fingerprint(archive), "full"
    ).retry_members


def test_malformed_member_is_reported_without_abandoning_later_members(
    persistence, tmp_path, monkeypatch
):
    archive = _archive(
        tmp_path / "source.zip",
        [
            ["0000000001-26-000001"],
            ["bad"],
            ["0000000001-26-000002"],
        ],
    )
    summary = _service(persistence, tmp_path).run(archive, confirm=True)
    assert summary.skipped_members == 1
    assert summary.filings_parsed == 2
    opened = _track_opens(monkeypatch)
    retry = _service(persistence, tmp_path).run(archive, confirm=True)
    assert opened == ["CIK0000000002.json"]
    assert retry.skipped_members == 1
    assert retry.filings_parsed == 0


def test_accession_aliases_are_counted_once_even_after_failure(persistence, tmp_path):
    accession = "0000000001-26-000001"
    archive = _archive(tmp_path / "source.zip", [[accession], [accession]])
    transport = make_transport()
    transport.get.side_effect = lambda *_a, **_kw: StubResponse(status_code=503)
    summary = _service(persistence, tmp_path, transport).run(archive, confirm=True)
    assert summary.discovered_rows == 2
    assert summary.filings_discovered == 1
    assert summary.duplicates == 1
    assert summary.skipped_resume == 0
    assert summary.failures == 1
    assert persistence.sec_ingestion.get(accession).attempts == 1


def test_targeted_cursor_does_not_advance_full_archive(
    persistence, tmp_path, monkeypatch
):
    archive = _archive(
        tmp_path / "source.zip",
        [
            ["0000000001-26-000001"],
            ["0000000001-26-000002"],
        ],
    )
    _service(persistence, tmp_path).run(
        archive, confirm=True, ciks=frozenset({"0000000002"})
    )
    opened = _track_opens(monkeypatch)
    summary = _service(persistence, tmp_path).run(archive, confirm=True)
    assert opened == ["CIK0000000001.json", "CIK0000000002.json"]
    assert summary.filings_parsed == 1
    assert summary.skipped_resume == 1


def test_invalid_alias_does_not_hide_later_valid_accession(persistence, tmp_path):
    accession = "0000000001-26-000001"
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for index, filing_date in enumerate(["invalid", "2026-06-15"]):
            archive.writestr(
                f"CIK{index + 1:010d}.json",
                json.dumps(
                    {
                        "filings": {
                            "recent": {
                                "accessionNumber": [accession],
                                "form": ["4"],
                                "filingDate": [filing_date],
                            }
                        },
                    }
                ),
            )
    summary = _service(persistence, tmp_path).run(path, confirm=True)
    assert summary.filings_discovered == 1
    assert summary.discovered_rows == 2
    assert summary.duplicates == summary.skipped_metadata == 1
    assert summary.filings_parsed == 1
    assert persistence.sec_ingestion.is_terminal(accession)


def test_failed_trade_batch_keeps_member_positions_retryable(
    persistence, tmp_path, monkeypatch
):
    archive = _archive(
        tmp_path / "source.zip",
        [
            ["0000000001-26-000001"],
            ["0000000001-26-000002"],
        ],
    )
    original = persistence.sec_ingestion.commit

    def unavailable(_entries):
        raise PersistenceError("unavailable")

    monkeypatch.setattr(persistence.sec_ingestion, "commit", unavailable)
    failed = _service(persistence, tmp_path).run(archive, confirm=True)
    assert failed.failures == 2
    progress = persistence.sec_ingestion.archive_progress(
        bulk_archive_fingerprint(archive), "full"
    )
    assert progress.retry_members == frozenset({0, 1})
    monkeypatch.setattr(persistence.sec_ingestion, "commit", original)
    reopened = _track_opens(monkeypatch)
    retried = _service(persistence, tmp_path).run(archive, confirm=True)
    assert reopened == ["CIK0000000001.json", "CIK0000000002.json"]
    assert retried.filings_parsed == 2
    assert retried.failures == 0


def test_empty_members_checkpoint_in_bounded_groups(persistence, tmp_path, monkeypatch):
    archive = _archive(tmp_path / "empty.zip", [[] for _ in range(451)])
    writes = []
    original = persistence.sec_ingestion.commit_archive_progress

    def commit(archive_id, scope, members):
        writes.append(len(members))
        return original(archive_id, scope, members)

    monkeypatch.setattr(persistence.sec_ingestion, "commit_archive_progress", commit)
    _service(persistence, tmp_path).run(archive, confirm=True)
    assert writes == [200, 200, 51]
    opened = _track_opens(monkeypatch)
    _service(persistence, tmp_path).run(archive, confirm=True)
    assert opened == []


def test_replaced_archive_starts_new_discovery_without_refetching_successes(
    persistence, tmp_path, monkeypatch
):
    first, second = "0000000001-26-000001", "0000000001-26-000002"
    path = _archive(tmp_path / "source.zip", [[first]])
    _service(persistence, tmp_path).run(path, confirm=True)
    _archive(path, [[second], [first]])
    opened = _track_opens(monkeypatch)
    summary = _service(persistence, tmp_path).run(path, confirm=True)
    assert opened == ["CIK0000000001.json", "CIK0000000002.json"]
    assert summary.filings_parsed == summary.skipped_resume == 1
    assert persistence.sec_ingestion.is_terminal(second)
