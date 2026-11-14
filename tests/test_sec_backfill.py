from __future__ import annotations

import pytest

from insider_scanner.core.sec_trade_mapping import SecTradeMappingError
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.services.sec_backfill import (
    SecBackfillConfirmationError,
    SecBackfillService,
    SecBackfillSummary,
)
from tests.sec_stubs import (
    FIXTURE_ZIP,  # three transaction filings plus one excluded Form 3
    VALID_FILING,
    make_client,
    make_transport,
)

BACKFILL_FILING = VALID_FILING.replace(b"2026-06-12", b"2024-01-01")


def _service(persistence, tmp_path, transport=None, **kw) -> SecBackfillService:
    # The ZIP spans 2024-2026; its generic XML must predate every indexed filing.
    transport = transport or make_transport(filing_bytes=BACKFILL_FILING)
    return SecBackfillService(
        persistence,
        client=make_client(transport),
        cache_root=tmp_path / "cache",
        checkpoint_path=tmp_path / "backfill.json",
        **kw,
    )


def test_run_without_confirm_raises_and_does_no_io(persistence, tmp_path):
    fetched: list[str] = []
    svc = _service(
        persistence,
        tmp_path,
        make_transport(filing_bytes=VALID_FILING, fetched=fetched),
    )
    with pytest.raises(SecBackfillConfirmationError):
        svc.run(FIXTURE_ZIP, confirm=False)
    assert fetched == []


def test_run_processes_all_fixture_records(persistence, tmp_path):
    svc = _service(persistence, tmp_path)
    summary = svc.run(FIXTURE_ZIP, confirm=True)
    assert isinstance(summary, SecBackfillSummary)
    assert summary.filings_discovered == 3
    assert summary.filings_parsed == 3
    assert not (tmp_path / "backfill.json").exists()


def test_rerun_skips_completed_accessions_via_checkpoint(persistence, tmp_path):
    seen = {"n": 0}

    def cancel_after_one() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    svc = _service(persistence, tmp_path)
    svc.run(FIXTURE_ZIP, confirm=True, cancelled=cancel_after_one)
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


def test_persistence_error_is_not_checkpointed_and_is_retried(
    persistence, tmp_path, monkeypatch
):
    def _boom(trades):
        raise PersistenceError("transient db failure")

    # Run 1: every upsert fails -> no accession is retired.
    monkeypatch.setattr(persistence.us_trades, "upsert", _boom)
    svc = _service(persistence, tmp_path)
    summary1 = svc.run(FIXTURE_ZIP, confirm=True)
    assert summary1.failures == 3
    assert summary1.transactions_inserted == 0
    assert not (tmp_path / "backfill.json").exists()  # nothing checkpointed

    # Run 2: upsert restored -> nothing skipped via resume; trades now persist.
    monkeypatch.undo()
    resume = _service(persistence, tmp_path)
    summary2 = resume.run(FIXTURE_ZIP, confirm=True)
    assert summary2.skipped_resume == 0
    assert summary2.transactions_inserted > 0


def test_mapping_error_is_not_checkpointed_and_recovers_after_mapper_fix(
    persistence, tmp_path, monkeypatch
):
    def _raise(filing, row):
        raise SecTradeMappingError("unmappable")

    monkeypatch.setattr(
        "insider_scanner.services.sec_backfill.ownership_filing_to_trades", _raise
    )
    seen = {"n": 0}

    def cancel_after_one() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    # Run 1: process one mapping failure, then interrupt without retiring it.
    svc = _service(persistence, tmp_path)
    first = svc.run(FIXTURE_ZIP, confirm=True, cancelled=cancel_after_one)
    assert first.failures >= 1
    assert not (tmp_path / "backfill.json").exists()
    assert first.outcomes[0].stage == "mapping"
    assert first.outcomes[0].accession

    # Run 2: repaired mapper sees the failed accession again and persists it.
    monkeypatch.undo()
    resume = _service(persistence, tmp_path)
    second = resume.run(FIXTURE_ZIP, confirm=True)
    assert second.skipped_resume == 0
    assert second.transactions_inserted > 0


def test_blocked_backfill_stops_and_preserves_completed_checkpoint(
    persistence, tmp_path
):
    import json
    import shutil

    from insider_scanner.core.sec_client import SecBlockedError
    from tests.sec_stubs import StubResponse

    transport = make_transport()
    original_get = transport.get.side_effect
    fetched = []

    def get(url, **kwargs):
        fetched.append(url)
        if len(fetched) > 1:
            return StubResponse(status_code=403)
        return original_get(url, **kwargs)

    transport.get.side_effect = get
    archive = tmp_path / "submissions.zip"
    shutil.copyfile(FIXTURE_ZIP, archive)
    service = _service(persistence, tmp_path, transport)
    with pytest.raises(SecBlockedError):
        service.run(archive, confirm=True)

    checkpoint = json.loads((tmp_path / "backfill.json").read_text(encoding="utf-8"))
    assert len(checkpoint["completed_accessions"]) == 1
    assert len(fetched) == 3
    archive.rename(tmp_path / "closed-archive.zip")  # ZIP handle closed on abort


def test_partial_backfill_keeps_successful_checkpoint_for_retry(persistence, tmp_path):
    from tests.sec_stubs import StubResponse

    transport = make_transport(filing_bytes=BACKFILL_FILING)
    original_get = transport.get.side_effect

    def get(url, **kwargs):
        if "/1318605/" in url:
            return StubResponse(status_code=503)
        return original_get(url, **kwargs)

    transport.get.side_effect = get
    first = _service(persistence, tmp_path, transport).run(FIXTURE_ZIP, confirm=True)
    assert first.failures == 1
    assert (tmp_path / "backfill.json").exists()
    second = _service(persistence, tmp_path).run(FIXTURE_ZIP, confirm=True)
    assert second.skipped_resume == 2
    assert second.filings_parsed == 1
    assert second.failures == 0


def test_unsupported_accession_is_terminal_on_resume(persistence, tmp_path):
    legacy = (
        b"<SEC-DOCUMENT>\n<DOCUMENT>\n<TYPE>4\n<TEXT>Paper filing</TEXT>\n</DOCUMENT>"
    )
    calls = 0

    def cancel_after_one():
        nonlocal calls
        calls += 1
        return calls > 1

    first = _service(persistence, tmp_path, make_transport(filing_bytes=legacy)).run(
        FIXTURE_ZIP, confirm=True, cancelled=cancel_after_one
    )
    assert first.unsupported == 1
    assert first.failures == 0
    second = _service(persistence, tmp_path).run(FIXTURE_ZIP, confirm=True)
    assert second.skipped_resume == 1
    assert second.filings_parsed == 2
