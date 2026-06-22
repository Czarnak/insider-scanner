from __future__ import annotations
from collections.abc import Iterator
from pathlib import Path
import pytest

from insider_scanner.services.context import PersistenceContext, open_persistence
from insider_scanner.services.sec_backfill import (
    SecBackfillService, SecBackfillSummary, SecBackfillConfirmationError,
)
from tests.test_sec_downloads import make_client, make_transport, VALID_FILING
from tests.test_sec_bulk import FIXTURE_DIR, FIXTURE_ZIP  # reuse the 4-record fixture


@pytest.fixture
def persistence(tmp_path: Path) -> Iterator[PersistenceContext]:
    ctx = open_persistence(database_file=tmp_path / "db.sqlite")
    try:
        yield ctx
    finally:
        ctx.close()


def _service(persistence, tmp_path, transport=None, **kw) -> SecBackfillService:
    transport = transport or make_transport(filing_bytes=VALID_FILING)
    return SecBackfillService(
        persistence,
        client=make_client(transport),
        cache_root=tmp_path / "cache",
        checkpoint_path=tmp_path / "backfill.json",
        **kw,
    )


def test_run_without_confirm_raises_and_does_no_io(persistence, tmp_path):
    fetched: list[str] = []
    svc = _service(persistence, tmp_path,
                   make_transport(filing_bytes=VALID_FILING, fetched=fetched))
    with pytest.raises(SecBackfillConfirmationError):
        svc.run(FIXTURE_ZIP, confirm=False)
    assert fetched == []


def test_run_processes_all_fixture_records(persistence, tmp_path):
    svc = _service(persistence, tmp_path)
    summary = svc.run(FIXTURE_ZIP, confirm=True)
    assert isinstance(summary, SecBackfillSummary)
    assert summary.filings_discovered == 4
    assert summary.filings_parsed == 4
    assert not (tmp_path / "backfill.json").exists()


def test_rerun_skips_completed_accessions_via_checkpoint(persistence, tmp_path):
    seen = {"n": 0}
    def cancel_after_one() -> bool:
        seen["n"] += 1
        return seen["n"] > 1
    svc = _service(persistence, tmp_path)
    first = svc.run(FIXTURE_ZIP, confirm=True, cancelled=cancel_after_one)
    assert (tmp_path / "backfill.json").exists()
    resume = _service(persistence, tmp_path)
    second = resume.run(FIXTURE_ZIP, confirm=True)
    assert second.skipped_resume >= 1
    assert second.filings_parsed >= 1


def test_cik_filter_limits_scope(persistence, tmp_path):
    svc = _service(persistence, tmp_path)
    summary = svc.run(FIXTURE_ZIP, confirm=True, ciks=frozenset({"0001318605"}))
    assert summary.filings_discovered == 1


def test_cleanup_can_be_disabled(persistence, tmp_path, monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(
        "insider_scanner.services.sec_backfill.purge_stale_cache",
        lambda *a, **k: calls.append(1) or 0,
    )
    svc = _service(persistence, tmp_path, cleanup=False)
    svc.run(FIXTURE_ZIP, confirm=True)
    assert calls == []
