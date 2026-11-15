"""Effective reads preserve raw provenance without counting restatements twice."""

import json
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
            "sec_detail_json": '{"security_title":"Common Stock","remarks":"Corrects the share count in the original report.","narrative_complete":true}',
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
def test_sec_amendment_replaces_matching_line_before_filters_and_limits(
    engine, reverse
):
    repo = UsTradeRepository(engine)
    original = native()
    second_lot = replace(
        original,
        sec_row_id="non-derivative:1",
        shares=5,
        value=1000,
        trade_date=date(2026, 1, 4),
    )
    amendment = amended(original)
    for rows in (
        [[amendment], [original, second_lot]]
        if reverse
        else [[original, second_lot], [amendment]]
    ):
        repo.upsert(rows)
    assert repo.query("AAPL") == [amendment, second_lot]
    assert repo.query_latest(1) == [amendment]
    assert repo.query("AAPL", end_date=original.filing_date) == [second_lot]
    assert len(repo.query_raw("AAPL")) == 3
    assert repo.query_raw("AAPL", include_superseded=False) == [amendment, second_lot]
    feed = FeedRepository(engine).query(FeedQuery(markets=(FeedMarket.US,)))
    assert len(feed.records) == 2


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


def test_amendment_under_new_ticker_supersedes_original_scraper_evidence(engine):
    repo = UsTradeRepository(engine)
    original = native()
    repo.upsert([original, us_trade(shares=10, value=2000)])
    repo.upsert([amended(original, ticker="MSFT")])
    assert repo.query("AAPL", sources=["secform4"]) == []
    assert repo.query_raw("AAPL", include_superseded=False) == []


def test_sec_amendment_with_rejected_rows_cannot_supersede_complete_original(
    persistence,
):
    from insider_scanner.core.sec_ingestion import SecFilingOutcome, SecFilingStatus
    from insider_scanner.persistence.sec_ingestion import SecIngestionWrite

    original = native()
    amendment = amended(original)
    invalid = replace(
        amendment, sec_row_id="non-derivative:1", price=-1, trade_date=date(2026, 1, 6)
    )
    persistence.us_trades.upsert([original])
    outcome = SecFilingOutcome(amendment.accession_number, SecFilingStatus.SUCCESS)
    result = persistence.sec_ingestion.commit(
        [SecIngestionWrite(outcome, (amendment, invalid))]
    )
    assert result.upserts.failed == 1
    assert original in persistence.us_trades.query("AAPL")
    persistence.sec_ingestion.commit(
        [SecIngestionWrite(outcome, (amendment, replace(invalid, price=200)))]
    )
    assert original not in persistence.us_trades.query("AAPL")


def test_sec_line_amendment_preserves_unchanged_original_transactions(engine):
    repo = UsTradeRepository(engine)
    original = native()
    unchanged = replace(
        original, sec_row_id="non-derivative:1", trade_date=date(2026, 1, 4)
    )
    amendment = amended(original)
    repo.upsert([original, unchanged, amendment])
    assert repo.query("AAPL") == [amendment, unchanged]


def test_sec_added_transaction_does_not_replace_same_day_original(engine):
    repo = UsTradeRepository(engine)
    original = native()
    addition = amended(
        original,
        sec_detail_json='{"security_title":"Common Stock","remarks":"Adds a previously omitted transaction."}',
    )
    repo.upsert([original, addition])
    assert len(repo.query("AAPL")) == 2


def test_sec_identical_restatement_replaces_only_one_repeated_lot(engine):
    repo = UsTradeRepository(engine)
    original = native()
    repeated = replace(original, sec_row_id="non-derivative:1")
    amendment = amended(original, shares=original.shares, value=original.value)
    repo.upsert([original, repeated, amendment])
    assert len(repo.query("AAPL")) == 2


def test_sec_unknown_changed_line_remains_visible_without_correction_evidence(engine):
    repo = UsTradeRepository(engine)
    original = native()
    unknown = amended(original, sec_detail_json='{"security_title":"Common Stock"}')
    repo.upsert([original, unknown])
    assert len(repo.query("AAPL")) == 2


def test_sec_original_submission_date_disambiguates_same_period_filings(engine):
    repo = UsTradeRepository(engine)
    original = native()
    other = replace(
        original, accession_number="0000320193-26-000003", filing_date=date(2026, 1, 6)
    )
    amendment = amended(original, original_submission_date=original.filing_date)
    repo.upsert([original, other, amendment])
    assert repo.query("AAPL") == [amendment, other]


def test_sec_nonfinancial_correction_cannot_authorize_changed_quantity(engine):
    repo = UsTradeRepository(engine)
    original = native()
    amendment = amended(
        original,
        sec_detail_json='{"security_title":"Common Stock","remarks":"Corrects the reporting owner address."}',
    )
    repo.upsert([original, amendment])
    assert len(repo.query("AAPL")) == 2


@pytest.mark.parametrize("reverse", [False, True])
def test_scraper_aggregate_is_stale_if_any_constituent_line_is_superseded(
    engine, reverse
):
    repo = UsTradeRepository(engine)
    original = native(shares=4, value=800, direct_or_indirect="D")
    unchanged = replace(
        original,
        sec_row_id="non-derivative:1",
        shares=6,
        value=1200,
        direct_or_indirect="I",
    )
    lots = [unchanged, original] if reverse else [original, unchanged]
    repo.upsert([*lots, us_trade(shares=10, value=2000)])
    repo.upsert([amended(original, shares=5, value=1000)])
    assert repo.query("AAPL", sources=["secform4"]) == []
    assert len(repo.query_raw("AAPL", include_superseded=False)) == 2


def test_incomplete_retained_narrative_cannot_authorize_changed_quantity(engine):
    repo = UsTradeRepository(engine)
    original = native()
    amendment = amended(
        original,
        sec_detail_json='{"security_title":"Common Stock","remarks":"Corrects the share count.","narrative_complete":false}',
    )
    repo.upsert([original, amendment])
    assert len(repo.query("AAPL")) == 2


def test_amendment_chain_keeps_untouched_original_line(engine):
    repo = UsTradeRepository(engine)
    original = native()
    untouched = replace(
        original, sec_row_id="non-derivative:1", trade_date=date(2026, 1, 4)
    )
    first = amended(original)
    last = amended(
        first,
        accession_number="0000320193-26-000004",
        filing_date=date(2026, 1, 9),
        shares=30,
        value=6000,
    )
    repo.upsert([last, first, original, untouched])
    assert repo.query("AAPL") == [last, untouched]


def test_original_submission_date_does_not_disambiguate_same_day_originals(engine):
    repo = UsTradeRepository(engine)
    original = native()
    other = replace(original, accession_number="0000320193-26-000003")
    repo.upsert(
        [
            original,
            other,
            amended(original, original_submission_date=original.filing_date),
        ]
    )
    assert len(repo.query("AAPL")) == 3


def test_unrelated_correction_text_cannot_authorize_unchanged_share_count(engine):
    repo = UsTradeRepository(engine)
    original = native()
    amendment = amended(
        original,
        sec_detail_json='{"security_title":"Common Stock","remarks":"Corrects the reporting owner address. The share count is unchanged."}',
    )
    repo.upsert([original, amendment])
    assert len(repo.query("AAPL")) == 2


@pytest.mark.parametrize(
    "owners",
    [
        [{"cik": "0000123456"}, {"cik": ""}],
        [{"cik": "0000123456"}, {"cik": None}],
        [{"cik": "0000123456"}, {"cik": "invalid"}],
        "not-a-list",
    ],
)
def test_incomplete_joint_owner_evidence_cannot_fall_back_to_primary_owner(
    engine, owners
):
    repo = UsTradeRepository(engine)
    original = native()
    detail = json.loads(original.sec_detail_json)
    detail["reporting_owners"] = owners
    amendment = amended(original, sec_detail_json=json.dumps(detail))
    repo.upsert([original, amendment])
    assert len(repo.query("AAPL")) == 2


def test_legacy_narrative_without_completeness_proof_cannot_supersede(engine):
    repo = UsTradeRepository(engine)
    original = native()
    amendment = amended(
        original,
        sec_detail_json='{"security_title":"Common Stock","footnote_ids":["F1"],"footnotes":{"F1":"Corrects the share count."}}',
    )
    repo.upsert([original, amendment])
    assert len(repo.query("AAPL")) == 2


def test_omission_wording_vetoes_even_complete_correction_narrative(engine):
    repo = UsTradeRepository(engine)
    original = native()
    detail = json.loads(original.sec_detail_json)
    detail["remarks"] = (
        "Corrects the share count following an omission in the original report."
    )
    repo.upsert([original, amended(original, sec_detail_json=json.dumps(detail))])
    assert len(repo.query("AAPL")) == 2


def test_migration_keeps_legacy_uncertainty_until_complete_source_reparse(tmp_path):
    engine = create_sqlite_engine(tmp_path / "legacy-narrative.sqlite3")
    try:
        bootstrap_database(engine, migrations=MIGRATIONS[:10])
        original = native()
        modern = amended(original)
        legacy_detail = json.loads(modern.sec_detail_json)
        del legacy_detail["narrative_complete"]
        legacy = replace(modern, sec_detail_json=json.dumps(legacy_detail))
        table = Table("us_trades", MetaData(), autoload_with=engine)
        with engine.begin() as connection:
            connection.execute(
                insert(table), [us_to_values(original), us_to_values(legacy)]
            )
            before = list(connection.execute(select(table)).mappings())
        bootstrap_database(engine)
        repo = UsTradeRepository(engine)
        assert len(repo.query("AAPL")) == 2
        with engine.connect() as connection:
            assert list(connection.execute(select(table)).mappings()) == before
        repo.upsert([modern])
        assert repo.query("AAPL") == [modern]
        assert len(repo.query_raw("AAPL")) == 2
    finally:
        engine.dispose()
