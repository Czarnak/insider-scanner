"""DB-first scan service tests with network-free source adapters."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.services import (
    CongressScanService,
    EuropeanScanService,
    ScanError,
    UsScanService,
    open_persistence,
)
from insider_scanner.services.common import LATEST_OVERLAP_COUNT


def _us(
    *,
    ticker: str = "AAPL",
    source: str = "secform4",
    filing_date: date = date(2026, 1, 10),
    trade_date: date = date(2026, 1, 9),
    insider_name: str = "Tim Cook",
) -> InsiderTrade:
    return InsiderTrade(
        ticker=ticker,
        company="Example Inc.",
        insider_name=insider_name,
        insider_title="Officer",
        trade_type="Buy",
        trade_date=trade_date,
        filing_date=filing_date,
        shares=10,
        price=100,
        value=1_000,
        source=source,
    )


def _congress(
    *,
    official: str = "Jane Doe",
    source: str = "house",
    filing_date: date = date(2026, 1, 10),
) -> CongressTrade:
    return CongressTrade(
        official_name=official,
        chamber="House" if source == "house" else "Senate",
        filing_date=filing_date,
        doc_id=f"{source}-{filing_date.isoformat()}",
        trade_date=filing_date - timedelta(days=1),
        asset_description="Example Inc. (EXM)",
        ticker="EXM",
        trade_type="Purchase",
        amount_range="$1,001 - $15,000",
        amount_low=1_001,
        amount_high=15_000,
        source=source,
    )


def _eu(
    *,
    isin: str = "GB0002875804",
    source: str = "rns",
    country: str = "UK",
    trade_date: date = date(2026, 1, 10),
) -> EuropeanInsiderTrade:
    return EuropeanInsiderTrade(
        isin=isin,
        issuer_name="Example PLC",
        country=country,
        regulatory_body={
            "UK": "FCA",
            "DE": "BaFin",
            "FR": "AMF",
            "NL": "AFM",
        }[country],
        insider_name="Jane Doe",
        position="Director",
        trade_date=trade_date,
        filing_date=trade_date + timedelta(days=1),
        trade_type="Buy",
        instrument_type="Share",
        volume=10,
        price=20,
        currency="EUR",
        total_value=200,
        source=source,
        source_url=f"https://example.test/{source}/{trade_date}",
    )


@pytest.fixture
def persistence(tmp_path: Path):
    context = open_persistence(tmp_path / "services.sqlite3")
    yield context
    context.close()


class BoundedAdapter:
    def __init__(self, factory):
        self.factory = factory
        self.calls: list[tuple[str | None, date, date, bool]] = []
        self.error: Exception | None = None
        self.on_call = None

    def __call__(
        self,
        identifier: str | None,
        start_date: date,
        end_date: date,
        use_cache: bool,
    ):
        self.calls.append((identifier, start_date, end_date, use_cache))
        if self.on_call:
            self.on_call()
        if self.error:
            raise self.error
        return self.factory(identifier, start_date, end_date)


class LatestAdapter:
    def __init__(self, trades):
        self.trades = list(trades)
        self.calls: list[tuple[int, bool, date | None, date | None]] = []
        self.error: Exception | None = None

    def __call__(
        self,
        count: int,
        use_cache: bool,
        start_date: date | None,
        end_date: date | None,
    ):
        self.calls.append((count, use_cache, start_date, end_date))
        if self.error:
            raise self.error
        return list(self.trades)


def test_us_first_repeat_subrange_and_both_side_expansion(persistence):
    adapter = BoundedAdapter(
        lambda ticker, start, end: [
            _us(
                ticker=ticker or "",
                source="wrong",
                filing_date=start,
                trade_date=start,
            )
        ]
    )
    service = UsScanService(persistence, adapters={"secform4": adapter})

    first = service.scan(
        " aapl ",
        sources=("secform4",),
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 10),
        use_cache=False,
    )
    repeat = service.scan(
        "AAPL",
        sources=("secform4",),
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 10),
    )
    subrange = service.scan(
        "AAPL",
        sources=("secform4",),
        start_date=date(2026, 1, 6),
        end_date=date(2026, 1, 9),
    )
    expanded = service.scan(
        "AAPL",
        sources=("secform4",),
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 12),
    )

    assert first[0].source == "secform4"
    assert repeat == first
    assert subrange == []
    assert [call[1:3] for call in adapter.calls] == [
        (date(2026, 1, 5), date(2026, 1, 10)),
        (date(2026, 1, 2), date(2026, 1, 4)),
        (date(2026, 1, 11), date(2026, 1, 12)),
    ]
    assert [trade.filing_date for trade in expanded] == [
        date(2026, 1, 11),
        date(2026, 1, 5),
        date(2026, 1, 2),
    ]


def test_us_unbounded_scan_uses_latest_refresh_without_infinite_coverage(persistence):
    bounded = {
        source: BoundedAdapter(lambda *_: []) for source in ("secform4", "openinsider")
    }
    latest = LatestAdapter(
        [
            _us(ticker="AAPL", source="openinsider"),
            _us(ticker="MSFT", source="openinsider"),
        ]
    )
    service = UsScanService(
        persistence,
        adapters=bounded,
        latest_adapters={"openinsider": latest},
    )

    first = service.scan(
        "aapl",
        sources=("secform4", "openinsider"),
        start_date=None,
        end_date=None,
    )
    second = service.scan(
        "AAPL",
        sources=("secform4", "openinsider"),
        start_date=None,
        end_date=None,
    )

    assert [trade.ticker for trade in first] == ["AAPL"]
    assert second == first
    assert latest.calls == [(LATEST_OVERLAP_COUNT, True, None, None)]
    assert all(not adapter.calls for adapter in bounded.values())
    assert persistence.coverage.get("us", "AAPL", "secform4") == ()
    assert persistence.coverage.get("us", "AAPL", "openinsider") == ()


def test_us_coverage_is_per_source_and_empty_success_closes_gap(persistence):
    sec = BoundedAdapter(lambda *_: [])
    oi = BoundedAdapter(
        lambda ticker, start, _end: [
            _us(ticker=ticker or "", source="openinsider", filing_date=start)
        ]
    )
    service = UsScanService(
        persistence,
        adapters={"secform4": sec, "openinsider": oi},
    )
    kwargs = {
        "ticker": "AAPL",
        "sources": ("secform4", "openinsider"),
        "start_date": date(2026, 2, 1),
        "end_date": date(2026, 2, 3),
    }

    service.scan(**kwargs)
    service.scan(**kwargs)

    assert len(sec.calls) == 1
    assert len(oi.calls) == 1
    assert persistence.coverage.get("us", "AAPL", "secform4") == (
        DateInterval(date(2026, 2, 1), date(2026, 2, 3)),
    )


def test_us_exception_wraps_context_and_leaves_gap_uncovered(persistence):
    adapter = BoundedAdapter(lambda *_: [])
    adapter.error = RuntimeError("source down")
    service = UsScanService(persistence, adapters={"secform4": adapter})

    with pytest.raises(ScanError, match="secform4.*2026-03-01.*2026-03-02") as exc:
        service.scan(
            "AAPL",
            sources=("secform4",),
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 2),
        )

    assert isinstance(exc.value.__cause__, RuntimeError)
    assert persistence.coverage.get("us", "AAPL", "secform4") == ()


def test_us_cancellation_during_call_discards_results_and_coverage(persistence):
    cancelled = False
    adapter = BoundedAdapter(
        lambda ticker, start, _end: [
            _us(ticker=ticker or "", source="secform4", filing_date=start)
        ]
    )

    def cancel():
        nonlocal cancelled
        cancelled = True

    adapter.on_call = cancel
    service = UsScanService(persistence, adapters={"secform4": adapter})

    result = service.scan(
        "AAPL",
        sources=("secform4",),
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 2),
        cancelled=lambda: cancelled,
    )

    assert result == []
    assert persistence.coverage.get("us", "AAPL", "secform4") == ()
    assert persistence.us_trades.query("AAPL") == []


def test_us_cancellation_between_sources_returns_stored_results(persistence):
    first = BoundedAdapter(
        lambda ticker, start, _end: [
            _us(ticker=ticker or "", source="secform4", filing_date=start)
        ]
    )
    second = BoundedAdapter(lambda *_: [])
    checks = iter((False, False, True))
    service = UsScanService(
        persistence,
        adapters={"secform4": first, "openinsider": second},
    )
    result = service.scan(
        "AAPL",
        sources=("secform4", "openinsider"),
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 2),
        cancelled=lambda: next(checks),
    )

    assert len(result) == 1
    assert second.calls == []
    assert persistence.coverage.get("us", "AAPL", "secform4") == (
        DateInterval(date(2026, 5, 1), date(2026, 5, 2)),
    )
    assert persistence.coverage.get("us", "AAPL", "openinsider") == ()


def test_us_restart_reuses_database_and_flags_congress_on_copies(tmp_path):
    database = tmp_path / "restart.sqlite3"
    adapter = BoundedAdapter(
        lambda ticker, start, _end: [
            _us(
                ticker=ticker or "",
                source="secform4",
                filing_date=start,
                insider_name="Pelosi Nancy",
            )
        ]
    )
    first_context = open_persistence(database)
    UsScanService(first_context, adapters={"secform4": adapter}).scan(
        "AAPL",
        sources=("secform4",),
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
    )
    first_context.close()

    second_context = open_persistence(database)
    try:
        result = UsScanService(second_context, adapters={"secform4": adapter}).scan(
            "AAPL",
            sources=("secform4",),
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
        )
        stored = second_context.us_trades.query("AAPL")
    finally:
        second_context.close()

    assert len(adapter.calls) == 1
    assert result[0].is_congress is True
    assert stored[0].is_congress is False


def test_congress_normalizes_all_and_named_official_with_source_isolation(
    persistence,
):
    house = BoundedAdapter(
        lambda official, start, _end: [
            _congress(
                official=official or "Jane Doe", source="house", filing_date=start
            )
        ]
    )
    senate = BoundedAdapter(
        lambda official, start, _end: [
            _congress(
                official=official or "John Roe",
                source="senate",
                filing_date=start,
            )
        ]
    )
    service = CongressScanService(
        persistence,
        adapters={"house": house, "senate": senate},
    )

    all_results = service.scan(
        " All ",
        sources=("house", "senate"),
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
    )
    named = service.scan(
        "  Jane   Doe ",
        sources=("house",),
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
    )

    assert house.calls[0][0] is None
    assert senate.calls[0][0] is None
    assert house.calls[1][0] == "Jane Doe"
    assert [trade.official_name for trade in all_results] == [
        "Jane Doe",
        "John Roe",
    ]
    assert [trade.official_name for trade in named] == ["Jane Doe"]


def test_congress_empty_success_repeat_and_ordering(persistence):
    house = BoundedAdapter(lambda *_: [])
    service = CongressScanService(persistence, adapters={"house": house})
    persistence.congress_trades.upsert(
        [
            _congress(filing_date=date(2026, 8, 1)),
            _congress(filing_date=date(2026, 8, 3)),
        ]
    )

    first = service.scan(
        "Jane Doe",
        sources=("house",),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 3),
    )
    second = service.scan(
        "Jane Doe",
        sources=("house",),
        start_date=date(2026, 8, 2),
        end_date=date(2026, 8, 3),
    )

    assert len(house.calls) == 1
    assert [trade.filing_date for trade in first] == [
        date(2026, 8, 3),
        date(2026, 8, 1),
    ]
    assert [trade.filing_date for trade in second] == [date(2026, 8, 3)]


def test_congress_unbounded_scan_uses_gui_six_month_finite_default(persistence):
    house = BoundedAdapter(lambda *_: [])
    service = CongressScanService(
        persistence,
        adapters={"house": house},
        today=lambda: date(2026, 6, 8),
    )

    service.scan(
        "All",
        sources=("house",),
        start_date=None,
        end_date=None,
    )

    assert house.calls == [(None, date(2025, 12, 8), date(2026, 6, 8), True)]
    assert persistence.coverage.get("congress", "*", "house") == (
        DateInterval(date(2025, 12, 8), date(2026, 6, 8)),
    )


@pytest.mark.parametrize(
    ("country", "expected"),
    [
        ("UK", {"rns"}),
        ("DE", {"bafin"}),
        ("FR", {"amf"}),
        ("NL", {"afm"}),
        ("All", {"rns", "bafin", "amf", "afm"}),
    ],
)
def test_eu_country_selects_expected_sources(persistence, country, expected):
    adapters = {
        source: BoundedAdapter(lambda *_: [])
        for source in ("rns", "bafin", "amf", "afm")
    }
    service = EuropeanScanService(persistence, adapters=adapters)

    service.scan(
        "gb0002875804",
        country=country,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
    )

    called = {source for source, adapter in adapters.items() if adapter.calls}
    assert called == expected


def test_eu_repeat_expansion_source_provenance_and_ordering(persistence):
    rns = BoundedAdapter(
        lambda isin, start, _end: [
            _eu(
                isin=isin or "",
                source="wrong",
                country="UK",
                trade_date=start,
            )
        ]
    )
    service = EuropeanScanService(persistence, adapters={"rns": rns})

    service.scan(
        "GB0002875804",
        country="UK",
        start_date=date(2026, 10, 5),
        end_date=date(2026, 10, 6),
    )
    repeated = service.scan(
        "GB0002875804",
        country="UK",
        start_date=date(2026, 10, 5),
        end_date=date(2026, 10, 6),
    )
    expanded = service.scan(
        "GB0002875804",
        country="UK",
        start_date=date(2026, 10, 3),
        end_date=date(2026, 10, 8),
    )

    assert repeated[0].source == "rns"
    assert [call[1:3] for call in rns.calls] == [
        (date(2026, 10, 5), date(2026, 10, 6)),
        (date(2026, 10, 3), date(2026, 10, 4)),
        (date(2026, 10, 7), date(2026, 10, 8)),
    ]
    assert [trade.trade_date for trade in expanded] == [
        date(2026, 10, 7),
        date(2026, 10, 5),
        date(2026, 10, 3),
    ]


def test_eu_bounded_amf_persists_and_returns_data_but_rechecks_range(persistence):
    amf = BoundedAdapter(
        lambda isin, start, _end: [
            _eu(
                isin=isin or "",
                source="amf",
                country="FR",
                trade_date=start,
            )
        ]
    )
    service = EuropeanScanService(persistence, adapters={"amf": amf})
    kwargs = {
        "isin": "FR0000131104",
        "country": "FR",
        "start_date": date(2026, 10, 1),
        "end_date": date(2026, 10, 31),
    }

    first = service.scan(**kwargs)
    second = service.scan(**kwargs)

    assert [trade.isin for trade in first] == ["FR0000131104"]
    assert second == first
    assert len(persistence.european_trades.query("FR0000131104", source="amf")) == 1
    assert persistence.coverage.get("eu", "FR0000131104", "amf") == ()
    assert len(amf.calls) == 2


@pytest.mark.parametrize(
    ("country", "source", "isin"),
    [
        ("UK", "rns", "GB0002875804"),
        ("DE", "bafin", "DE0005557508"),
        ("NL", "afm", "NL0000009538"),
    ],
)
def test_eu_bounded_trade_date_sources_reuse_coverage(
    persistence,
    country,
    source,
    isin,
):
    adapter = BoundedAdapter(lambda *_: [])
    service = EuropeanScanService(persistence, adapters={source: adapter})
    kwargs = {
        "isin": isin,
        "country": country,
        "start_date": date(2026, 10, 1),
        "end_date": date(2026, 10, 31),
    }

    service.scan(**kwargs)
    service.scan(**kwargs)

    assert len(adapter.calls) == 1
    assert persistence.coverage.get("eu", isin, source) == (
        DateInterval(date(2026, 10, 1), date(2026, 10, 31)),
    )


def test_eu_unbounded_scan_uses_latest_refresh_without_infinite_coverage(persistence):
    bounded = {
        source: BoundedAdapter(lambda *_: [])
        for source in ("rns", "bafin", "amf", "afm")
    }
    latest = LatestAdapter(
        [
            _eu(isin="GB0002875804", source="rns", country="UK"),
            _eu(isin="GB0000000001", source="rns", country="UK"),
        ]
    )
    service = EuropeanScanService(
        persistence,
        adapters=bounded,
        latest_adapters={"rns": latest},
    )

    first = service.scan(
        "gb0002875804",
        country="All",
        start_date=None,
        end_date=None,
    )
    second = service.scan(
        "GB0002875804",
        country="All",
        start_date=None,
        end_date=None,
    )

    assert [trade.isin for trade in first] == ["GB0002875804"]
    assert second == first
    assert latest.calls == [(LATEST_OVERLAP_COUNT, True, None, None)]
    assert all(not adapter.calls for adapter in bounded.values())
    for source in bounded:
        assert persistence.coverage.get("eu", "GB0002875804", source) == ()


def test_eu_cross_source_duplicate_is_coalesced_and_queryable_by_each_source(
    persistence,
):
    rns = BoundedAdapter(
        lambda isin, start, _end: [
            _eu(
                isin=isin or "",
                source="rns",
                country="UK",
                trade_date=start,
            )
        ]
    )
    amf = BoundedAdapter(
        lambda isin, start, _end: [
            _eu(
                isin=isin or "",
                source="amf",
                country="FR",
                trade_date=start,
            )
        ]
    )
    service = EuropeanScanService(
        persistence,
        adapters={"rns": rns, "amf": amf},
    )

    result = service.scan(
        "FR0000131104",
        country="All",
        sources=("rns", "amf"),
        start_date=date(2026, 10, 10),
        end_date=date(2026, 10, 10),
    )

    assert len(result) == 1
    assert persistence.european_trades.query("FR0000131104", source="rns") == result
    assert persistence.european_trades.query("FR0000131104", source="amf") == result
    assert persistence.coverage.get("eu", "FR0000131104", "rns") == (
        DateInterval(date(2026, 10, 10), date(2026, 10, 10)),
    )
    assert persistence.coverage.get("eu", "FR0000131104", "amf") == ()


def test_latest_us_uses_ttl_overlap_and_deterministic_clock(persistence):
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    latest = LatestAdapter(
        [
            _us(
                ticker="MSFT",
                source="openinsider",
                filing_date=date(2026, 11, 1),
            ),
            _us(
                ticker="AAPL",
                source="openinsider",
                filing_date=date(2026, 10, 31),
            ),
        ]
    )
    service = UsScanService(
        persistence,
        adapters={},
        latest_adapters={"openinsider": latest},
        clock=lambda: now,
    )

    first = service.latest(count=1, sources=("openinsider",), use_cache=False)
    fresh = service.latest(count=2, sources=("openinsider",))
    stale_service = UsScanService(
        persistence,
        adapters={},
        latest_adapters={"openinsider": latest},
        clock=lambda: now + timedelta(hours=2),
    )
    stale_service.latest(count=1, sources=("openinsider",))

    assert latest.calls == [
        (LATEST_OVERLAP_COUNT, False, None, None),
        (LATEST_OVERLAP_COUNT, True, None, None),
    ]
    assert [trade.ticker for trade in first] == ["MSFT"]
    assert [trade.ticker for trade in fresh] == ["MSFT", "AAPL"]


def test_latest_us_larger_count_refreshes_after_smaller_capacity(persistence):
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    latest = LatestAdapter([])
    service = UsScanService(
        persistence,
        adapters={},
        latest_adapters={"openinsider": latest},
        clock=lambda: now,
        latest_overlap_count=2,
    )

    service.latest(count=2)
    service.latest(count=3)
    service.latest(count=3)

    assert [call[0] for call in latest.calls] == [2, 3]


def test_latest_us_with_dates_delegates_to_bounded_ticker_scan(persistence):
    bounded = BoundedAdapter(
        lambda ticker, start, _end: [
            _us(ticker=ticker or "", source="openinsider", filing_date=start)
        ]
    )
    latest = LatestAdapter([])
    service = UsScanService(
        persistence,
        adapters={"openinsider": bounded},
        latest_adapters={"openinsider": latest},
    )

    result = service.latest(
        count=10,
        ticker="AAPL",
        sources=("openinsider",),
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 30),
    )

    assert len(result) == 1
    assert len(bounded.calls) == 1
    assert latest.calls == []
    assert persistence.coverage.get("us", "AAPL", "openinsider") == (
        DateInterval(date(2026, 11, 1), date(2026, 11, 30)),
    )


def test_latest_eu_refresh_is_per_source(persistence):
    now = datetime(2026, 12, 1, 12, tzinfo=UTC)
    rns_latest = LatestAdapter([_eu(source="rns", country="UK")])
    bafin_latest = LatestAdapter(
        [
            _eu(
                isin="DE0005557508",
                source="bafin",
                country="DE",
                trade_date=date(2026, 1, 11),
            )
        ]
    )
    service = EuropeanScanService(
        persistence,
        adapters={},
        latest_adapters={
            "rns": rns_latest,
            "bafin": bafin_latest,
        },
        clock=lambda: now,
    )

    service.latest(count=10, country="UK")
    service.latest(count=10, country="UK")
    service.latest(count=10, country="DE")

    assert len(bafin_latest.calls) == 1
    assert len(rns_latest.calls) == 1


def test_latest_eu_larger_count_refreshes_after_smaller_capacity(persistence):
    now = datetime(2026, 12, 1, 12, tzinfo=UTC)
    latest = LatestAdapter([])
    service = EuropeanScanService(
        persistence,
        adapters={},
        latest_adapters={"rns": latest},
        clock=lambda: now,
        latest_overlap_count=2,
    )

    service.latest(count=2, country="UK")
    service.latest(count=3, country="UK")
    service.latest(count=3, country="UK")

    assert [call[0] for call in latest.calls] == [2, 3]


@pytest.mark.parametrize(
    ("country", "source", "isin"),
    [
        ("UK", "rns", "GB0002875804"),
        ("FR", "amf", "FR0000131104"),
    ],
)
def test_latest_eu_with_dates_delegates_to_bounded_isin_scan(
    persistence,
    country,
    source,
    isin,
):
    bounded = BoundedAdapter(
        lambda identifier, start, _end: [
            _eu(
                isin=identifier or "",
                source=source,
                country=country,
                trade_date=start,
            )
        ]
    )
    latest = LatestAdapter([])
    service = EuropeanScanService(
        persistence,
        adapters={source: bounded},
        latest_adapters={} if source == "amf" else {source: latest},
    )

    result = service.latest(
        count=10,
        isin=isin,
        country=country,
        sources=(source,),
        start_date=date(2026, 12, 1),
        end_date=date(2026, 12, 31),
    )

    assert len(result) == 1
    assert len(bounded.calls) == 1
    assert latest.calls == []
    expected_coverage = (
        ()
        if source == "amf"
        else (DateInterval(date(2026, 12, 1), date(2026, 12, 31)),)
    )
    assert persistence.coverage.get("eu", isin, source) == expected_coverage


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda service: service.scan(
                "",
                sources=("secform4",),
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 2),
            ),
            "ticker",
        ),
        (
            lambda service: service.scan(
                "AAPL",
                sources=(),
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 2),
            ),
            "sources",
        ),
        (
            lambda service: service.scan(
                "AAPL",
                sources=("unknown",),
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 2),
            ),
            "unknown",
        ),
        (
            lambda service: service.scan(
                "AAPL",
                sources=("secform4",),
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 1),
            ),
            "start_date",
        ),
        (
            lambda service: service.scan(
                "AAPL",
                sources=("secform4",),
                start_date=date(2026, 1, 1),
                end_date=None,
            ),
            "both",
        ),
    ],
)
def test_us_validation(persistence, call, message):
    service = UsScanService(
        persistence,
        adapters={"secform4": BoundedAdapter(lambda *_: [])},
    )
    with pytest.raises(ValueError, match=message):
        call(service)


@pytest.mark.parametrize("official", ["", "   "])
def test_congress_rejects_empty_official(persistence, official):
    service = CongressScanService(
        persistence,
        adapters={"house": BoundedAdapter(lambda *_: [])},
    )
    with pytest.raises(ValueError, match="official"):
        service.scan(
            official,
            sources=("house",),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        )


@pytest.mark.parametrize(
    ("isin", "message"),
    [("", "ISIN"), ("GB123", "12"), ("GB00028758045", "12")],
)
def test_eu_rejects_invalid_isin(persistence, isin, message):
    service = EuropeanScanService(
        persistence,
        adapters={"rns": BoundedAdapter(lambda *_: [])},
    )
    with pytest.raises(ValueError, match=message):
        service.scan(
            isin,
            country="UK",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        )


def test_latest_validates_positive_count_and_complete_date_range(persistence):
    service = UsScanService(
        persistence,
        adapters={},
        latest_adapters={"openinsider": LatestAdapter([])},
    )
    with pytest.raises(ValueError, match="count"):
        service.latest(count=0)
    with pytest.raises(ValueError, match="both"):
        service.latest(count=10, start_date=date(2026, 1, 1))


def test_latest_us_with_dates_and_no_ticker_uses_bounded_latest_adapter(persistence):
    latest = LatestAdapter(
        [
            _us(
                ticker="AAPL",
                source="openinsider",
                filing_date=date(2026, 1, 2),
            )
        ]
    )
    service = UsScanService(
        persistence,
        adapters={},
        latest_adapters={"openinsider": latest},
    )

    result = service.latest(
        count=10,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )

    assert [trade.ticker for trade in result] == ["AAPL"]
    assert latest.calls == [
        (LATEST_OVERLAP_COUNT, True, date(2026, 1, 1), date(2026, 1, 2))
    ]
    assert persistence.coverage.get("us", "*", "openinsider") == ()


def test_latest_eu_with_dates_and_no_isin_uses_bounded_latest_adapter(persistence):
    latest = LatestAdapter(
        [
            _eu(
                source="rns",
                country="UK",
                trade_date=date(2026, 1, 2),
            )
        ]
    )
    service = EuropeanScanService(
        persistence,
        adapters={},
        latest_adapters={"rns": latest},
    )

    result = service.latest(
        count=10,
        country="UK",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )

    assert [trade.country for trade in result] == ["UK"]
    assert latest.calls == [
        (LATEST_OVERLAP_COUNT, True, date(2026, 1, 1), date(2026, 1, 2))
    ]
    assert persistence.coverage.get("eu", "*", "rns") == ()


def test_latest_eu_bounded_fr_uses_full_trade_date_adapter(persistence):
    bounded = BoundedAdapter(
        lambda _identifier, start, _end: [
            _eu(
                isin="FR0000131104",
                source="amf",
                country="FR",
                trade_date=start,
            )
        ]
    )
    latest = LatestAdapter([])
    service = EuropeanScanService(
        persistence,
        adapters={"amf": bounded},
        latest_adapters={"amf": latest},
    )

    result = service.latest(
        count=10,
        country="FR",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )

    assert [trade.country for trade in result] == ["FR"]
    assert bounded.calls == [(None, date(2026, 1, 1), date(2026, 1, 31), True)]
    assert latest.calls == []
    assert persistence.coverage.get("eu", "*", "amf") == ()


def test_scan_error_from_latest_preserves_source_and_cause(persistence):
    latest = LatestAdapter([])
    latest.error = OSError("offline")
    service = UsScanService(
        persistence,
        adapters={},
        latest_adapters={"openinsider": latest},
    )

    with pytest.raises(ScanError, match="openinsider.*latest") as exc:
        service.latest(count=10)

    assert isinstance(exc.value.__cause__, OSError)
    assert (
        persistence.refresh.get(
            "us", "*", "openinsider", f"latest:{LATEST_OVERLAP_COUNT}"
        )
        is None
    )


def test_explicit_empty_adapter_mapping_does_not_load_defaults(persistence):
    service = UsScanService(
        persistence,
        adapters={},
        latest_adapters={},
    )

    with pytest.raises(ValueError, match="unknown"):
        service.scan(
            "AAPL",
            sources=("secform4",),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        )


def test_single_source_string_is_not_split_into_characters(persistence):
    adapter = BoundedAdapter(lambda *_: [])
    service = UsScanService(
        persistence,
        adapters={"secform4": adapter},
    )

    service.scan(
        "AAPL",
        sources="secform4",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )

    assert len(adapter.calls) == 1


def test_eu_all_latest_uses_only_latest_capable_sources(persistence):
    adapters = {source: LatestAdapter([]) for source in ("rns", "bafin", "amf")}
    service = EuropeanScanService(
        persistence,
        adapters={},
        latest_adapters=adapters,
    )

    service.latest(count=10, country="All")

    assert all(len(adapter.calls) == 1 for adapter in adapters.values())
    assert all(
        persistence.refresh.get("eu", "*", source, f"latest:{LATEST_OVERLAP_COUNT}")
        is not None
        for source in adapters
    )
    assert (
        persistence.refresh.get("eu", "*", "afm", f"latest:{LATEST_OVERLAP_COUNT}")
        is None
    )


def test_eu_nl_latest_rejects_without_setting_refresh(persistence):
    service = EuropeanScanService(
        persistence,
        adapters={"afm": BoundedAdapter(lambda *_: [])},
        latest_adapters={
            source: LatestAdapter([]) for source in ("rns", "bafin", "amf")
        },
    )

    with pytest.raises(ValueError, match="latest.*NL|NL.*latest"):
        service.latest(count=10, country="NL")

    assert (
        persistence.refresh.get("eu", "*", "afm", f"latest:{LATEST_OVERLAP_COUNT}")
        is None
    )


def test_eu_latest_rejects_explicit_afm_adapter(persistence):
    afm = LatestAdapter([])
    service = EuropeanScanService(
        persistence,
        adapters={"afm": BoundedAdapter(lambda *_: [])},
        latest_adapters={"afm": afm},
    )

    with pytest.raises(ValueError, match="latest"):
        service.latest(count=10, country="All", sources=("afm",))

    assert afm.calls == []
    assert (
        persistence.refresh.get("eu", "*", "afm", f"latest:{LATEST_OVERLAP_COUNT}")
        is None
    )


def test_open_persistence_wraps_unexpected_bootstrap_failure(
    tmp_path,
    monkeypatch,
):
    def fail(_engine):
        raise RuntimeError("broken bootstrap")

    monkeypatch.setattr(
        "insider_scanner.services.context.bootstrap_database",
        fail,
    )

    with pytest.raises(PersistenceError, match="open") as exc:
        open_persistence(tmp_path / "broken.sqlite3")

    assert isinstance(exc.value.__cause__, RuntimeError)
