"""Archive positions retain retries without changing the accession ledger."""

import sqlite3
from dataclasses import replace

import pytest
from sqlalchemy import MetaData, Table, event, insert, inspect
from sqlalchemy.exc import OperationalError

from insider_scanner.persistence.bootstrap import bootstrap_database
from insider_scanner.persistence.engine import create_sqlite_engine
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.mappings import us_to_values
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.sec_ingestion import SecArchiveProgress
from insider_scanner.services.context import open_persistence
from tests.test_sec_ingestion_ledger import ACCESSION, successful_write


def test_archive_progress_is_scoped_and_retry_members_survive_reopen(tmp_path):
    database = tmp_path / "progress.db"
    context = open_persistence(database)
    try:
        repository = context.sec_ingestion
        repository.commit([successful_write()])
        repository.commit_archive_progress("archive", "full", [(0, True), (1, False)])
        repository.commit_archive_progress("archive", "target", [(5, False)])
        repository.commit_archive_progress("changed", "full", [(8, False)])
    finally:
        context.close()

    reopened = open_persistence(database)
    try:
        repository = reopened.sec_ingestion
        assert repository.archive_progress("archive", "full") == SecArchiveProgress(
            2, frozenset({0})
        )
        assert repository.archive_progress("archive", "target").next_member == 6
        assert repository.archive_progress("changed", "full").next_member == 9
        assert repository.archive_progress("new", "full") == SecArchiveProgress()
        repository.commit_archive_progress("archive", "full", [(0, False)])
        assert repository.archive_progress("archive", "full") == SecArchiveProgress(2)
        assert repository.is_terminal(ACCESSION)
    finally:
        reopened.close()


def test_archive_retry_and_cursor_write_roll_back_together(persistence):
    repository = persistence.sec_ingestion

    def fail_retry(_connection, _cursor, statement, *_args):
        if statement.startswith("INSERT INTO sec_archive_retries"):
            raise OperationalError("retry insert", {}, sqlite3.OperationalError("test"))

    event.listen(persistence.engine, "before_cursor_execute", fail_retry)
    try:
        with pytest.raises(PersistenceError):
            repository.commit_archive_progress("archive", "full", [(4, True)])
    finally:
        event.remove(persistence.engine, "before_cursor_execute", fail_retry)
    assert repository.archive_progress("archive", "full") == SecArchiveProgress()


def test_upgrade_adds_archive_progress_without_losing_existing_filings(tmp_path):
    engine = create_sqlite_engine(tmp_path / "upgrade.db")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:9])
        from insider_scanner.persistence.sec_ingestion import SecIngestionRepository

        repository = SecIngestionRepository(engine)
        historical = Table("us_trades", MetaData(), autoload_with=engine)
        write = successful_write()
        with engine.begin() as connection:
            connection.execute(insert(historical), us_to_values(write.trades[0]))
        repository.commit([replace(write, trades=())])
        bootstrap_database(engine)
        assert {"sec_archive_progress", "sec_archive_retries"} <= set(
            inspect(engine).get_table_names()
        )
        assert repository.is_terminal(ACCESSION)
        assert repository.archive_progress("archive", "full") == SecArchiveProgress()
    finally:
        engine.dispose()
