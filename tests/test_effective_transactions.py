"""Effective reads preserve raw provenance without counting restatements twice."""

from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import MetaData, Table, insert, select

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.feed import FeedMarket, FeedQuery, FeedRepository
from insider_scanner.persistence.mappings import us_to_values
from insider_scanner.persistence.migrations import MIGRATIONS
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    UsTradeRepository,
)
from tests.factories import congress_trade, sec_trade, us_trade


def native(**kwargs):
    return sec_trade(
        **{
            "cik_issuer": "0000320193",
            "cik_insider": "0000123456",
            "period_of_report": date(2026, 1, 5),
            "form_type": "4",
            "transaction_code": "S",
            **kwargs,
        }
    )


def amended(original, **kwargs):
    return replace(
        original,
        **{
            "accession_number": "0000320193-26-000002",
            "sec_row_id": "non-derivative:0",
            "form_type": "4/A",
            "is_amendment": True,
            "filing_date": date(2026, 1, 8),
            "shares": 20,
            "value": 4000,
            **kwargs,
        },
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_sec_amendment_replaces_entire_filing_before_filters_and_limits(
    engine, reverse
):
    repo = UsTradeRepository(engine)
    original = native()
    second_lot = replace(original, sec_row_id="non-derivative:1", shares=5, value=1000)
    amendment = amended(original)
    for rows in (
        [[amendment], [original, second_lot]]
        if reverse
        else [[original, second_lot], [amendment]]
    ):
        repo.upsert(rows)
    assert repo.query("AAPL") == [amendment]
    assert repo.query_latest(1) == [amendment]
    assert repo.query("AAPL", end_date=original.filing_date) == []
    assert len(repo.query_raw("AAPL")) == 3
    assert repo.query_raw("AAPL", include_superseded=False) == [amendment]
    feed = FeedRepository(engine).query(FeedQuery(markets=(FeedMarket.US,)))
    assert len(feed.records) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_effective_cross_source_reads_preserve_raw_rows_and_native_lots(
    engine, reverse
):
    repo = UsTradeRepository(engine)
    first = native(shares=4, value=800.25)
    second = replace(first, sec_row_id="non-derivative:1", shares=6, value=1200.25)
    scraper = us_trade(shares=10, value=2001)
    repo.upsert([scraper, first, second] if reverse else [first, second, scraper])
    assert len(repo.query("AAPL")) == 2
    assert len(repo.query_raw("AAPL")) == 3
    assert repo.query("AAPL", sources=["secform4"]) == [scraper]
    assert len(FeedRepository(engine).query(FeedQuery()).records) == 2
    assert repo.upsert([scraper, first, second]).skipped == 3


def test_amendment_also_replaces_scraped_original_observation(engine):
    repo = UsTradeRepository(engine)
    original = native()
    scraper = us_trade(shares=10, value=2000)
    amendment = amended(original)
    repo.upsert([original, scraper, amendment])
    assert repo.query("AAPL") == [amendment]
    assert repo.query_raw("AAPL", include_superseded=False) == [amendment]
    assert len(repo.query_raw("AAPL")) == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"cik_insider": "0000999999"},
        {"period_of_report": date(2026, 1, 6)},
        {"period_of_report": None},
        {"cik_issuer": ""},
        {"form_type": "5/A"},
    ],
)
def test_incomplete_or_different_amendment_identity_never_hides_original(
    engine, changes
):
    repo = UsTradeRepository(engine)
    original = native()
    repo.upsert([original, amended(original, **changes)])
    assert len(repo.query("AAPL")) == 2


def test_multiple_original_filings_are_not_guessed_as_one_amendment_chain(engine):
    repo = UsTradeRepository(engine)
    original = native()
    other = replace(original, accession_number="0000320193-26-000003", shares=30)
    repo.upsert([original, other, amended(original)])
    assert len(repo.query("AAPL")) == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"trade_type": "Buy"},
        {"filing_date": date(2026, 1, 9)},
        {"shares": 11},
        {"value": 3000},
        {"value_missing": True},
    ],
)
def test_unequal_scraper_observations_are_not_hidden(engine, changes):
    repo = UsTradeRepository(engine)
    repo.upsert([native(), us_trade(**{"shares": 10, "value": 2000, **changes})])
    assert len(repo.query("AAPL")) == 2


def senate(**changes):
    trade = congress_trade(source="senate", chamber="Senate", **changes)
    # Assign explicitly so the RED run exercises behavior before model support.
    trade.report_title = "Periodic Transaction Report for 01/30/2026"
    trade.period_of_report = date(2026, 1, 30)
    trade.is_amendment = False
    trade.filing_complete = True
    return trade


@pytest.mark.parametrize("reverse", [False, True])
def test_senate_effective_filing_retains_repeated_lots_and_raw_original(
    engine, reverse
):
    repo = CongressTradeRepository(engine)
    original = senate(source_row_id="1")
    amendment = senate(
        doc_id="amended", source_row_id="1", filing_date=date(2026, 2, 3)
    )
    amendment.is_amendment = True
    amendment.report_title += " (Amendment)"
    lot = replace(amendment, source_row_id="2")
    for rows in (
        [[amendment, lot], [original]] if reverse else [[original], [amendment, lot]]
    ):
        repo.upsert(rows)
    assert len(repo.query()) == 2
    assert len(repo.query_raw()) == 3
    assert repo.query(end_date=original.filing_date) == []
    assert len(FeedRepository(engine).query(FeedQuery()).records) == 2


def test_partial_senate_amendment_does_not_replace_original(engine):
    repo = CongressTradeRepository(engine)
    original = senate()
    amendment = senate(doc_id="amended", filing_date=date(2026, 2, 3))
    amendment.is_amendment = True
    amendment.filing_complete = False
    repo.upsert([original, amendment])
    assert len(repo.query()) == 2


def test_upgrade_preserves_raw_rows_and_repairs_effective_visibility(tmp_path):
    engine = create_sqlite_engine(tmp_path / "historical.sqlite3")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:10])
        table = Table("us_trades", MetaData(), autoload_with=engine)
        original = native()
        amendment = amended(original)
        with engine.begin() as connection:
            connection.execute(
                insert(table), [us_to_values(original), us_to_values(amendment)]
            )
            before = list(connection.execute(select(table)).mappings())
        bootstrap_database(engine)
        assert UsTradeRepository(engine).query("AAPL") == [amendment]
        with engine.begin() as connection:
            assert list(connection.execute(select(table)).mappings()) == before
        bootstrap_database(engine)
        assert len(UsTradeRepository(engine).query_raw("AAPL")) == 2
    finally:
        engine.dispose()


def test_same_day_unchanged_amendment_does_not_restore_scraper_duplicate(engine):
    repo = UsTradeRepository(engine)
    original = native()
    amendment = amended(
        original, shares=10, value=2000, filing_date=original.filing_date
    )
    repo.upsert([original, amendment, us_trade(shares=10, value=2000)])
    assert repo.query("AAPL") == [amendment]


def test_reparse_moves_native_identity_without_hiding_previous_ticker_scraper(engine):
    repo = UsTradeRepository(engine)
    original = native(cik_issuer="", cik_insider="")
    scraper = us_trade(shares=10, value=2000)
    repo.upsert([original, scraper])
    assert repo.query("AAPL") == [original]
    repo.upsert([replace(original, ticker="MSFT")])
    assert repo.query("AAPL") == [scraper]


@pytest.mark.parametrize("latest", [False, True])
def test_source_generators_are_used_once(engine, latest):
    repo = UsTradeRepository(engine)
    scraper = us_trade(shares=10, value=2000)
    repo.upsert([native(), scraper])
    sources = (name for name in ["secform4"])
    result = (
        repo.query_latest(10, sources=sources)
        if latest
        else repo.query("AAPL", sources=sources)
    )
    assert result == [scraper]


def test_metadata_free_import_cannot_promote_partial_senate_amendment(engine):
    repo = CongressTradeRepository(engine)
    original = senate(source_row_id="1")
    partial = senate(doc_id="amended", source_row_id="1", filing_date=date(2026, 2, 3))
    partial.is_amendment = True
    partial.filing_complete = False
    repo.upsert([original, partial])
    repo.upsert(
        [
            replace(
                partial,
                report_title="",
                period_of_report=None,
                is_amendment=False,
                filing_complete=True,
            )
        ]
    )
    assert len(repo.query()) == 2
