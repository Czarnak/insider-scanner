"""Failed database commits identify every affected filing in durable logs."""

from insider_scanner.core.sec_ingestion import SecFilingStatus
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.services.sec_persistence import SecTradeBatch


def test_failed_batch_logs_every_accession_without_exception_text(caplog):
    accessions = ("0000320193-26-000001", "0000789019-26-000002")

    def fail(_entries):
        raise PersistenceError("database unavailable with private-token=secret")

    batch = SecTradeBatch(fail)
    for accession in accessions:
        batch.add(accession, [])
    outcomes = batch.take_outcomes(flush=True)

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "insider_scanner.sec_persistence"
    ]
    assert len(messages) == len(accessions)
    for accession in accessions:
        assert any(
            f"accession={accession}" in message
            and "stage=persist" in message
            and "reason=PersistenceError" in message
            for message in messages
        )
    assert "private-token" not in caplog.text
    assert "secret" not in caplog.text
    assert [outcome.accession for outcome in outcomes] == list(accessions)
    assert all(
        outcome.status is SecFilingStatus.RETRYABLE_FAILURE for outcome in outcomes
    )
    assert batch.upserts.inserted == 0
