"""Refresh scans without losing historical coverage or partial source results."""

from datetime import UTC, date, datetime, timedelta

import pytest

from insider_scanner.core.congress_identity import identity_for
from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.services import CongressScanService, UsScanService
from tests.factories import congress_trade, us_trade


@pytest.mark.parametrize("domain", ["us", "congress"])
def test_no_cache_refetches_an_already_covered_interval(persistence, domain):
    calls = []

    def fetch(*args):
        calls.append(args)
        return []

    if domain == "us":
        service = UsScanService(persistence, adapters={"openinsider": fetch})
        identifier, source = "AAPL", "openinsider"
    else:
        service = CongressScanService(persistence, adapters={"house": fetch})
        identifier, source = "Jane Doe", "house"
    kwargs = {
        "sources": (source,),
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 1, 5),
    }

    service.scan(identifier, **kwargs)
    service.scan(identifier, **kwargs)
    service.scan(identifier, **kwargs, use_cache=False)

    assert calls == [
        (identifier, date(2025, 1, 1), date(2025, 1, 5), True),
        (identifier, date(2025, 1, 1), date(2025, 1, 5), False),
    ]


def test_latest_no_cache_bypasses_a_fresh_refresh_timestamp(persistence):
    calls = []

    def fetch(*args):
        calls.append(args)
        return []

    service = UsScanService(
        persistence,
        latest_adapters={"openinsider": fetch},
        clock=lambda: datetime(2026, 6, 8, tzinfo=UTC),
    )
    service.latest(count=10)
    service.latest(count=10)
    service.latest(count=10, use_cache=False)

    assert [call[1] for call in calls] == [True, False]


def _service(persistence, domain, fetch, clock):
    if domain == "us":
        return (
            UsScanService(persistence, adapters={"openinsider": fetch}, clock=clock),
            "AAPL",
            "openinsider",
        )
    return (
        CongressScanService(persistence, adapters={"house": fetch}, clock=clock),
        "Jane Doe",
        "house",
    )


@pytest.mark.parametrize("domain", ["us", "congress"])
def test_recent_filing_dates_refresh_after_ttl_and_history_stays_covered(
    persistence, domain
):
    now = datetime(2026, 6, 8, 9, tzinfo=UTC)
    calls = []

    def fetch(*args):
        calls.append(args)
        return []

    service, identifier, source = _service(persistence, domain, fetch, lambda: now)
    kwargs = {
        "sources": (source,),
        "start_date": date(2026, 5, 1),
        "end_date": now.date(),
    }
    service.scan(identifier, **kwargs)
    service.scan(identifier, **kwargs)
    now += timedelta(hours=2)
    service.scan(identifier, **kwargs)

    assert calls == [
        (identifier, date(2026, 5, 1), date(2026, 6, 8), True),
        (identifier, date(2026, 6, 2), date(2026, 6, 8), True),
    ]
    coverage_key = identity_for(identifier).key if domain == "congress" else identifier
    assert persistence.coverage.get(domain, coverage_key, source) == (
        DateInterval(date(2026, 5, 1), date(2026, 6, 7)),
    )


@pytest.mark.parametrize("domain", ["us", "congress"])
def test_today_remains_uncovered_and_later_filing_is_returned(persistence, domain):
    now = datetime(2026, 6, 8, 9, tzinfo=UTC)
    calls = []

    def fetch(*args):
        calls.append(args)
        if len(calls) == 1:
            return []
        trade = (
            us_trade(ticker="AAPL", source="openinsider")
            if domain == "us"
            else congress_trade(official_name="Jane Doe", source="house")
        )
        trade.filing_date = now.date()
        return [trade]

    service, identifier, source = _service(persistence, domain, fetch, lambda: now)
    kwargs = {"sources": (source,), "start_date": now.date(), "end_date": now.date()}
    assert service.scan(identifier, **kwargs) == []
    assert service.scan(identifier, **kwargs) == []
    now += timedelta(hours=2)
    assert len(service.scan(identifier, **kwargs)) == 1
    assert len(calls) == 2
    assert persistence.coverage.get(domain, identifier, source) == ()


@pytest.mark.parametrize("domain", ["us", "congress"])
def test_partial_recent_interval_has_no_freshness_and_retries_immediately(
    persistence, domain
):
    now = datetime(2026, 6, 8, 9, tzinfo=UTC)
    calls = []

    def fetch(*args):
        calls.append(args)
        return FetchResult(issues=[FetchIssue("result_limit")])

    service, identifier, source = _service(persistence, domain, fetch, lambda: now)
    kwargs = {"sources": (source,), "start_date": now.date(), "end_date": now.date()}
    assert not service.scan(identifier, **kwargs).complete
    assert not service.scan(identifier, **kwargs).complete
    assert len(calls) == 2
    assert (
        persistence.refresh.get(
            domain, identifier, source, "filing-tail:2026-06-08:2026-06-08"
        )
        is None
    )


@pytest.mark.parametrize("domain", ["us", "congress"])
def test_future_scan_dates_are_rejected_before_fetch(persistence, domain):
    calls = []
    service, identifier, source = _service(
        persistence,
        domain,
        lambda *args: calls.append(args) or [],
        lambda: datetime(2026, 6, 8, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="future"):
        service.scan(
            identifier,
            sources=(source,),
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 9),
        )
    assert calls == []


def test_default_ticker_scan_rejects_latest_only_source(persistence):
    service = UsScanService(
        persistence, adapters={}, latest_adapters={"latest_only": lambda *_: []}
    )
    with pytest.raises(ValueError, match="unknown source"):
        service.scan("AAPL", sources=("latest_only",), start_date=None, end_date=None)


@pytest.mark.parametrize("domain", ["us", "congress"])
@pytest.mark.parametrize("recent", [False, True])
def test_incomplete_forced_scan_invalidates_prior_completion(
    persistence, domain, recent
):
    now = datetime(2026, 6, 8, 9, tzinfo=UTC)
    day = now.date() if recent else date(2025, 1, 1)
    calls = []

    def fetch(*args):
        calls.append(args)
        if len(calls) == 2:
            return FetchResult(issues=[FetchIssue("result_limit")])
        return []

    service, identifier, source = _service(persistence, domain, fetch, lambda: now)
    kwargs = {"sources": (source,), "start_date": day, "end_date": day}
    assert service.scan(identifier, **kwargs).complete
    assert not service.scan(identifier, **kwargs, use_cache=False).complete
    assert service.scan(identifier, **kwargs).complete
    assert len(calls) == 3


def test_incomplete_forced_latest_invalidates_all_capacity_modes(persistence):
    calls = []

    def fetch(*args):
        calls.append(args)
        if len(calls) == 2:
            return FetchResult(issues=[FetchIssue("result_limit")])
        return []

    service = UsScanService(persistence, latest_adapters={"openinsider": fetch})
    assert service.latest(count=500).complete
    assert not service.latest(count=10, use_cache=False).complete
    assert service.latest(count=500).complete
    assert len(calls) == 3
