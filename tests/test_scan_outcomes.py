"""Incomplete sources never suppress retries or discard another source's rows."""

from datetime import date

import pytest

from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult, FetchStatus
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.services import CongressScanService, UsScanService
from tests.factories import congress_trade, us_trade

DAY = date(2026, 1, 10)


@pytest.mark.parametrize("domain", ["us", "congress"])
def test_partial_data_is_persisted_but_interval_is_retried(persistence, domain):
    trade = (
        us_trade(filing_date=DAY) if domain == "us" else congress_trade(filing_date=DAY)
    )
    identifier = trade.ticker if domain == "us" else trade.official_name
    source = "openinsider" if domain == "us" else "house"
    calls = []

    def fetch(*args):
        calls.append(args)
        return FetchResult([trade], issues=[FetchIssue("result_limit")])

    service_type = UsScanService if domain == "us" else CongressScanService
    service = service_type(persistence, adapters={source: fetch})
    for _ in range(2):
        result = service.scan(
            identifier, sources=[source], start_date=DAY, end_date=DAY
        )
        assert len(result) == 1
        assert result.status == FetchStatus.PARTIAL
        assert result.issues[0].source == source
        assert result.issues[0].reason == "result_limit"
    assert len(calls) == 2
    assert persistence.coverage.get(domain, identifier, source) == ()


@pytest.mark.parametrize("domain", ["us", "congress"])
@pytest.mark.parametrize("failure_first", [True, False])
def test_failing_source_retains_other_source_and_its_coverage(
    persistence, domain, failure_first
):
    trade = (
        us_trade(filing_date=DAY) if domain == "us" else congress_trade(filing_date=DAY)
    )
    identifier = trade.ticker if domain == "us" else trade.official_name

    def fail(*_args):
        raise TimeoutError("not included in diagnostics")

    failed = "openinsider" if domain == "us" else "senate"
    good = "secform4" if domain == "us" else "house"
    adapters = {failed: fail, good: lambda *_: [trade]}
    sources = [failed, good] if failure_first else [good, failed]
    service_type = UsScanService if domain == "us" else CongressScanService
    result = service_type(persistence, adapters=adapters).scan(
        identifier, sources=sources, start_date=DAY, end_date=DAY
    )
    assert len(result) == 1
    assert result[0].source == good
    assert result.status == FetchStatus.PARTIAL
    assert result.issues == (
        FetchIssue("TimeoutError", failed, f"{identifier} {DAY}..{DAY}"),
    )
    assert persistence.coverage.get(domain, identifier, failed) == ()
    assert persistence.coverage.get(domain, identifier, good) == (
        DateInterval(DAY, DAY),
    )


def test_partial_latest_is_retried_and_keeps_downloaded_rows(persistence):
    calls = []

    def fetch(*args):
        calls.append(args)
        return FetchResult(
            [us_trade(filing_date=DAY)], issues=[FetchIssue("page_failed")]
        )

    service = UsScanService(persistence, latest_adapters={"openinsider": fetch})
    for _ in range(2):
        result = service.latest(count=10)
        assert len(result) == 1
        assert result.status == FetchStatus.PARTIAL
    assert len(calls) == 2
    assert persistence.refresh.get("us", "*", "openinsider", "latest:200") is None


def test_latest_ticker_limit_preserves_incomplete_metadata(persistence):
    trade = us_trade(filing_date=DAY)
    service = UsScanService(
        persistence,
        adapters={
            "openinsider": lambda *_: FetchResult(
                [trade], issues=[FetchIssue("result_limit")]
            )
        },
    )
    result = service.latest(ticker=trade.ticker, count=1, start_date=DAY, end_date=DAY)
    assert len(result) == 1
    assert result.status == FetchStatus.PARTIAL
    assert result.issues[0].reason == "result_limit"


def test_unsupported_adapter_cannot_claim_empty_coverage(persistence):
    service = CongressScanService(
        persistence,
        adapters={"house": lambda *_: FetchResult(status=FetchStatus.UNSUPPORTED)},
    )
    result = service.scan("*", sources=["house"], start_date=DAY, end_date=DAY)
    assert result.status == FetchStatus.UNSUPPORTED
    assert not result.issues[0].retryable
    assert persistence.coverage.get("congress", "*", "house") == ()
