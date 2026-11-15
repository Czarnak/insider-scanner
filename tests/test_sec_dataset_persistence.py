"""Dataset and XML representations never double count the same accession."""

from dataclasses import replace

import pytest
from sqlalchemy import event

from insider_scanner.core.sec_ingestion import SecFilingOutcome, SecFilingStatus
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.sec_ingestion import SecIngestionWrite
from tests.factories import sec_trade

ACCESSION = "0000320193-26-000001"


def dataset_write(*, accession=ACCESSION, ids=("71", "99")):
    return SecIngestionWrite(
        SecFilingOutcome(accession, SecFilingStatus.SUCCESS, "dataset"),
        tuple(
            sec_trade(
                accession_number=accession,
                sec_row_id=f"{accession}:dataset:nonDerivative:{key}",
            )
            for key in ids
        ),
        representation="dataset",
    )


def xml_write(*, trades=None):
    return SecIngestionWrite(
        SecFilingOutcome(ACCESSION, SecFilingStatus.SUCCESS),
        (sec_trade(),) if trades is None else trades,
    )


def test_dataset_reimport_is_idempotent_and_removes_retired_surrogate_keys(persistence):
    persistence.sec_ingestion.commit([dataset_write()])
    repeated = persistence.sec_ingestion.commit([dataset_write()])
    assert repeated.upserts.skipped == 2
    persistence.sec_ingestion.commit([dataset_write(ids=("99",))])
    assert [t.sec_row_id for t in persistence.us_trades.query_latest(10)] == [
        f"{ACCESSION}:dataset:nonDerivative:99"
    ]
    assert persistence.sec_ingestion.get(ACCESSION).stage == "dataset"


@pytest.mark.parametrize("legacy", [False, True])
def test_dataset_never_degrades_xml_even_without_ledger(persistence, legacy):
    if legacy:
        persistence.us_trades.upsert([sec_trade(price=333)])
    else:
        persistence.sec_ingestion.commit([xml_write(trades=(sec_trade(price=333),))])
    result = persistence.sec_ingestion.commit([dataset_write()])
    assert result.upserts.inserted == 0
    assert persistence.us_trades.query_latest(10) == [sec_trade(price=333)]
    assert persistence.sec_ingestion.get(ACCESSION).stage != "dataset"


def test_empty_xml_success_cannot_be_replaced_by_dataset(persistence):
    persistence.sec_ingestion.commit([xml_write(trades=())])
    persistence.sec_ingestion.commit([dataset_write()])
    assert persistence.us_trades.query_latest(10) == []


@pytest.mark.parametrize("empty", [False, True])
def test_successful_xml_repair_replaces_only_its_dataset_accession(persistence, empty):
    other = "0000320193-26-000002"
    persistence.sec_ingestion.commit([dataset_write(), dataset_write(accession=other)])
    persistence.sec_ingestion.commit([xml_write(trades=() if empty else None)])
    rows = persistence.us_trades.query_latest(10)
    assert len(rows) == (2 if empty else 3)
    assert [t for t in rows if t.accession_number == ACCESSION] == (
        [] if empty else [sec_trade()]
    )
    assert persistence.sec_ingestion.get(ACCESSION).stage == ""


def test_failed_xml_repair_retains_dataset_and_no_partial_xml_rows(persistence):
    persistence.sec_ingestion.commit([dataset_write()])
    before = persistence.us_trades.query_latest(10)
    with pytest.raises(PersistenceError, match="representation"):
        persistence.sec_ingestion.commit(
            [xml_write(trades=(sec_trade(), sec_trade(sec_row_id="bad", price=-1)))]
        )
    assert persistence.us_trades.query_latest(10) == before
    assert persistence.sec_ingestion.get(ACCESSION).stage == "dataset"


def test_failed_dataset_reimport_retains_previous_rows_and_ledger(persistence):
    original = dataset_write()
    persistence.sec_ingestion.commit([original])
    invalid = replace(original, trades=(replace(original.trades[0], price=-1),))
    with pytest.raises(PersistenceError, match="representation"):
        persistence.sec_ingestion.commit([invalid])
    assert len(persistence.us_trades.query_latest(10)) == 2
    assert persistence.sec_ingestion.get(ACCESSION).attempts == 1


def test_ledger_failure_rolls_back_dataset_deletion(persistence):
    persistence.sec_ingestion.commit([dataset_write()])
    before = persistence.us_trades.query_latest(10)

    def interrupt(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO sec_ingested_filings"):
            raise KeyboardInterrupt

    event.listen(persistence.engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            persistence.sec_ingestion.commit([xml_write()])
    finally:
        event.remove(persistence.engine, "before_cursor_execute", interrupt)
    assert persistence.us_trades.query_latest(10) == before


def test_dataset_write_rejects_unowned_or_xml_identity(persistence):
    original = dataset_write()
    with pytest.raises(ValueError, match="dataset"):
        persistence.sec_ingestion.commit([replace(original, trades=(sec_trade(),))])
    assert persistence.us_trades.query_latest(10) == []


def test_complete_dataset_replaces_partial_retryable_xml(persistence):
    persistence.sec_ingestion.commit(
        [xml_write(trades=(sec_trade(), sec_trade(sec_row_id="bad", price=-1)))]
    )
    assert not persistence.sec_ingestion.is_terminal(ACCESSION)
    persistence.sec_ingestion.commit([dataset_write()])
    rows = persistence.us_trades.query_latest(10)
    assert len(rows) == 2
    assert all(":dataset:" in row.sec_row_id for row in rows)
    assert persistence.sec_ingestion.get(ACCESSION).stage == "dataset"


def test_xml_repair_cannot_publish_another_accession(persistence):
    persistence.sec_ingestion.commit([dataset_write()])
    with pytest.raises(ValueError, match="accession"):
        persistence.sec_ingestion.commit(
            [xml_write(trades=(sec_trade(accession_number="0000320193-26-000099"),))]
        )
    assert len(persistence.us_trades.query_latest(10)) == 2
    assert persistence.sec_ingestion.get(ACCESSION).stage == "dataset"
