"""Partial persistence never proves source coverage or hides rejected records."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from insider_scanner.core.fetch_outcome import FetchResult, FetchStatus
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.services import (
    CongressScanService,
    EuropeanScanService,
    UsScanService,
)
from insider_scanner.services.common import bounded_refresh_mode
from tests.factories import congress_trade, eu_trade, us_trade

DAY = date(2026, 1, 10)
NOW = datetime(2026, 1, 11, tzinfo=UTC)


def _records(domain):
    factory = {"us": us_trade, "congress": congress_trade, "eu": eu_trade}[domain]
    valid = factory(trade_date=DAY, filing_date=DAY)
    rejected = (
        replace(valid, doc_id="rejected", amount_low=-1)
        if domain == "congress"
        else replace(valid, insider_name="Rejected row", price=-1)
    )
    return valid, rejected


def _service(persistence, domain, fetch):
    if domain == "us":
        return (
            UsScanService(
                persistence,
                adapters={"openinsider": fetch},
                latest_adapters={"openinsider": fetch},
                clock=lambda: NOW,
            ),
            "AAPL",
            "openinsider",
        )
    if domain == "congress":
        return (
            CongressScanService(
                persistence, adapters={"house": fetch}, clock=lambda: NOW
            ),
            "*",
            "house",
        )
    return (
        EuropeanScanService(
            persistence,
            adapters={"rns": fetch},
            latest_adapters={"rns": fetch},
            bounded_coverage_sources={"rns"},
            clock=lambda: NOW,
        ),
        "GB0002875804",
        "rns",
    )


@pytest.mark.parametrize("domain", ["us", "congress", "eu"])
@pytest.mark.parametrize("all_rejected", [False, True])
def test_rejected_scan_rows_are_reported_and_interval_remains_uncovered(
    persistence, domain, all_rejected
):
    valid, rejected = _records(domain)
    records = [rejected] if all_rejected else [valid, rejected]
    calls = []

    def fetch(*args):
        calls.append(args)
        return records

    service, identifier, source = _service(persistence, domain, fetch)
    for _ in range(2):
        result = service.scan(
            identifier, sources=[source], start_date=DAY, end_date=DAY
        )
        assert isinstance(result, FetchResult)
        assert len(result) == (0 if all_rejected else 1)
        assert result.status == (
            FetchStatus.TRANSIENT_FAILURE if all_rejected else FetchStatus.PARTIAL
        )
        assert len(result.issues) == 1
        issue = result.issues[0]
        assert issue.source == source
        assert issue.identifier == (
            f"{identifier} {DAY}..{DAY}: row {0 if all_rejected else 1}"
        )
        assert "persistence_error:" in issue.reason
        assert "nonnegative" in issue.reason
        assert (".amount_low" if domain == "congress" else ".price") in issue.reason
        assert not issue.retryable
    assert len(calls) == 2
    assert persistence.coverage.get(domain, identifier, source) == ()
    assert (
        persistence.refresh.get(
            domain,
            identifier,
            source,
            bounded_refresh_mode(DateInterval(DAY, DAY)),
        )
        is None
    )


@pytest.mark.parametrize(
    ("domain", "mode"),
    [
        ("us", "latest"),
        ("us", "latest-range"),
        ("eu", "latest"),
        ("eu", "latest-range"),
        ("eu", "scan-unbounded"),
        ("eu", "latest-isin-range"),
    ],
)
def test_latest_paths_keep_good_rows_and_do_not_stamp_partial_persistence_fresh(
    persistence, domain, mode
):
    valid, rejected = _records(domain)
    calls = []

    def fetch(*args):
        calls.append(args)
        return [valid, rejected]

    service, identifier, source = _service(persistence, domain, fetch)
    for _ in range(2):
        if mode == "scan-unbounded":
            result = service.scan(
                identifier, sources=[source], start_date=None, end_date=None
            )
        else:
            kwargs = {"count": 10, "sources": [source]}
            if "range" in mode:
                kwargs.update(start_date=DAY, end_date=DAY)
            if mode == "latest-isin-range":
                kwargs["isin"] = identifier
            result = service.latest(**kwargs)
        assert len(result) == 1
        assert result.status == FetchStatus.PARTIAL
        assert len(result.issues) == 1
        assert result.issues[0].source == source
        assert result.issues[0].identifier.endswith(": row 1")
        assert "nonnegative" in result.issues[0].reason
        assert ".price" in result.issues[0].reason
    assert len(calls) == 2
    assert persistence.refresh.get(domain, "*", source, "latest:200") is None
