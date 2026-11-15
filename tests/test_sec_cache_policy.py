"""Bulk ingestion can use the bounded parsing pipeline without cache I/O."""

from insider_scanner.core import sec_ingestion
from insider_scanner.core.sec_index import parse_master_index
from tests.sec_stubs import INDEX_TWO_ROWS, make_client, make_transport


def test_uncached_ingestion_persists_without_cache_work(tmp_path, monkeypatch):
    row = parse_master_index(INDEX_TWO_ROWS)[0]
    root = tmp_path / "unused-cache"
    calls = []

    def unexpected(*args, **kwargs):
        raise AssertionError("cache filesystem must not be touched")

    monkeypatch.setattr(sec_ingestion, "promote_validated_filing", unexpected)
    monkeypatch.setattr(sec_ingestion, "quarantine_failed_download", unexpected)
    counters = sec_ingestion.new_counters()

    outcome = sec_ingestion.process_filing_row(
        row,
        client=make_client(make_transport()),
        cache_root=root,
        counters=counters,
        cache=False,
        on_filing_parsed=lambda row, filing: calls.append(filing),
    )

    assert outcome.complete
    assert len(calls) == counters["parsed"] == counters["downloaded"] == 1
    assert not root.exists()


def test_uncached_parse_failure_does_not_quarantine(tmp_path, monkeypatch):
    row = parse_master_index(INDEX_TWO_ROWS)[0]

    def unexpected(*args, **kwargs):
        raise AssertionError("cache filesystem must not be touched")

    monkeypatch.setattr(sec_ingestion, "quarantine_failed_download", unexpected)
    outcome = sec_ingestion.process_filing_row(
        row,
        client=make_client(make_transport(filing_bytes=b"broken document")),
        cache_root=tmp_path / "unused-cache",
        counters=sec_ingestion.new_counters(),
        cache=False,
        retain_failed_downloads=True,
    )

    assert outcome.status is sec_ingestion.SecFilingStatus.PERMANENT_FAILURE
    assert not (tmp_path / "unused-cache").exists()
