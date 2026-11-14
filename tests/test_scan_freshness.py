"""Refresh scans without losing historical coverage or partial source results."""

from datetime import UTC, date, datetime

import pytest

from insider_scanner.services import CongressScanService, UsScanService


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
    kwargs = dict(
        sources=(source,), start_date=date(2025, 1, 1), end_date=date(2025, 1, 5)
    )

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
