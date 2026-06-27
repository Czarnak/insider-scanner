from __future__ import annotations

import json
from datetime import date

import pytest

from insider_scanner.core.models import InsiderTrade
from insider_scanner.services.context import open_persistence
from insider_scanner.services.sec_comparison import (
    DEFAULT_LEGACY_SOURCES,
    SEC_EDGAR_SOURCE,
    SecComparisonService,
    SecComparisonTarget,
    render_report,
)


@pytest.fixture
def persistence(tmp_path):
    context = open_persistence(tmp_path / "comparison.sqlite3")
    try:
        yield context
    finally:
        context.close()


def _trade(**overrides) -> InsiderTrade:
    values = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "insider_name": "Tim Cook",
        "insider_title": "CEO",
        "trade_type": "Sell",
        "trade_date": date(2026, 1, 5),
        "filing_date": date(2026, 1, 7),
        "shares": 10.0,
        "price": 200.0,
        "value": 2_000.0,
        "shares_owned_after": 500_000.0,
        "source": SEC_EDGAR_SOURCE,
        "edgar_url": "https://sec.test/aapl",
        "accession_number": "0000320193-26-000001",
        "sec_row_id": "non-derivative:0",
    }
    values.update(overrides)
    return InsiderTrade(**values)  # type: ignore[arg-type]


def test_compare_reports_matched_and_unmatched_rows_by_legacy_source(persistence):
    persistence.us_trades.upsert(
        [
            _trade(),
            _trade(
                insider_name="SEC Only",
                trade_type="Buy",
                shares=5.0,
                value=1_000.0,
                accession_number="0000320193-26-000002",
                sec_row_id="non-derivative:1",
            ),
            _trade(
                source="secform4",
                accession_number="",
                sec_row_id="",
                edgar_url="",
            ),
            _trade(
                source="secform4",
                insider_name="Legacy Only",
                shares=7.0,
                value=1_400.0,
                accession_number="",
                sec_row_id="",
                edgar_url="",
            ),
            _trade(
                source="openinsider",
                accession_number="",
                sec_row_id="",
                edgar_url="",
            ),
        ]
    )

    report = SecComparisonService(persistence).compare(
        targets=(
            SecComparisonTarget(
                ticker="AAPL",
                start_date=date(2026, 1, 7),
                end_date=date(2026, 1, 7),
            ),
        )
    )

    assert report.legacy_sources == DEFAULT_LEGACY_SOURCES
    result = report.results[0]
    assert result.target.ticker == "AAPL"
    assert result.sec_count == 2

    secform4 = result.summary_for("secform4")
    assert secform4.legacy_count == 2
    assert secform4.matched_count == 1
    assert [row.insider_name for row in secform4.sec_only] == ["SEC Only"]
    assert [row.insider_name for row in secform4.legacy_only] == ["Legacy Only"]

    openinsider = result.summary_for("openinsider")
    assert openinsider.legacy_count == 1
    assert openinsider.matched_count == 1
    assert openinsider.sec_only[0].insider_name == "SEC Only"
    assert openinsider.legacy_only == ()



def test_compare_treats_different_filing_dates_as_unmatched(persistence):
    persistence.us_trades.upsert(
        [
            _trade(
                filing_date=date(2026, 1, 7),
                value=2_000.0,
                source=SEC_EDGAR_SOURCE,
            ),
            _trade(
                source="secform4",
                filing_date=date(2026, 1, 8),
                value=2_000.0,
                accession_number="",
                sec_row_id="",
                edgar_url="",
            ),
        ]
    )

    report = SecComparisonService(persistence).compare(
        targets=(SecComparisonTarget("AAPL", date(2026, 1, 7), date(2026, 1, 8)),),
        legacy_sources=("secform4",),
    )

    summary = report.results[0].summary_for("secform4")
    assert summary.matched_count == 0
    assert [row.filing_date for row in summary.sec_only] == [date(2026, 1, 7)]
    assert [row.filing_date for row in summary.legacy_only] == [date(2026, 1, 8)]


def test_compare_treats_different_values_as_unmatched(persistence):
    persistence.us_trades.upsert(
        [
            _trade(value=2_000.0, source=SEC_EDGAR_SOURCE),
            _trade(
                source="secform4",
                value=2_500.0,
                accession_number="",
                sec_row_id="",
                edgar_url="",
            ),
        ]
    )

    report = SecComparisonService(persistence).compare(
        targets=(SecComparisonTarget("AAPL", date(2026, 1, 7), date(2026, 1, 7)),),
        legacy_sources=("secform4",),
    )

    summary = report.results[0].summary_for("secform4")
    assert summary.matched_count == 0
    assert [row.value for row in summary.sec_only] == [2_000.0]
    assert [row.value for row in summary.legacy_only] == [2_500.0]
def test_compare_validates_targets_and_legacy_sources(persistence):
    service = SecComparisonService(persistence)

    with pytest.raises(ValueError, match="at least one target"):
        service.compare(targets=())

    with pytest.raises(ValueError, match="ticker must not be empty"):
        SecComparisonTarget(" ", date(2026, 1, 7), date(2026, 1, 7))

    with pytest.raises(ValueError, match="start_date must be on or before end_date"):
        SecComparisonTarget("AAPL", date(2026, 1, 8), date(2026, 1, 7))

    with pytest.raises(ValueError, match="legacy_sources must not include sec_edgar"):
        service.compare(
            targets=(SecComparisonTarget("AAPL", date(2026, 1, 7), date(2026, 1, 7)),),
            legacy_sources=(SEC_EDGAR_SOURCE,),
        )


def test_render_report_outputs_deterministic_markdown_text_and_json(persistence):
    persistence.us_trades.upsert(
        [
            _trade(),
            _trade(
                source="secform4",
                accession_number="",
                sec_row_id="",
                edgar_url="",
            ),
        ]
    )
    report = SecComparisonService(persistence).compare(
        targets=(SecComparisonTarget(" aapl ", date(2026, 1, 7), date(2026, 1, 7)),),
        legacy_sources=("secform4",),
    )

    markdown = render_report(report, "markdown")
    text = render_report(report, "text")
    payload = json.loads(render_report(report, "json"))

    assert markdown.splitlines()[:4] == [
        "# SEC EDGAR Comparison Report",
        "",
        "Legacy sources: secform4",
        "",
    ]
    assert "## AAPL 2026-01-07..2026-01-07" in markdown
    assert "- secform4: SEC=1 legacy=1 matched=1 SEC-only=0 legacy-only=0" in text
    assert payload["legacy_sources"] == ["secform4"]
    assert payload["results"][0]["target"]["ticker"] == "AAPL"
    assert payload["results"][0]["summaries"][0]["matched_count"] == 1


def test_render_report_rejects_unknown_format(persistence):
    report = SecComparisonService(persistence).compare(
        targets=(SecComparisonTarget("AAPL", date(2026, 1, 7), date(2026, 1, 7)),),
        legacy_sources=("secform4",),
    )

    with pytest.raises(ValueError, match="unknown comparison report format"):
        render_report(report, "html")
