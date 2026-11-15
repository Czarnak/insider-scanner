from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.core.prices.service import get_price_history
from insider_scanner.persistence.schema import price_history, scan_coverage


@pytest.fixture
def e() -> Engine:
    engine = create_engine("sqlite://")
    price_history.create(engine)
    scan_coverage.create(engine)
    yield engine
    engine.dispose()


class FakeSource:
    name = "fake"

    def __init__(self):
        self.calls: list[tuple[date, date]] = []

    def fetch_daily(self, symbol, start, end):
        self.calls.append((start, end))
        bars = [PriceBar(symbol.upper(), start, 1, 1, 1, 1, 1)]
        if start != end:
            bars.append(PriceBar(symbol.upper(), end, 1, 1, 1, 1, 1))
        return bars


def test_first_call_fetches_full_range_and_caches(e):
    src = FakeSource()
    bars = get_price_history(
        "AAPL", date(2026, 1, 1), date(2026, 1, 31), source=src, engine=e
    )
    assert src.calls == [(date(2026, 1, 1), date(2026, 1, 31))]
    assert len(bars) == 2
    # second identical call hits cache only -> no new source call
    bars2 = get_price_history(
        "AAPL", date(2026, 1, 1), date(2026, 1, 31), source=src, engine=e
    )
    assert src.calls == [(date(2026, 1, 1), date(2026, 1, 31))]  # unchanged
    assert len(bars2) == 2


def test_widening_range_fetches_only_edges(e):
    src = FakeSource()
    get_price_history(
        "AAPL", date(2026, 1, 10), date(2026, 1, 20), source=src, engine=e
    )
    src.calls.clear()
    get_price_history("AAPL", date(2026, 1, 1), date(2026, 1, 31), source=src, engine=e)
    # only the two edge gaps, never the already-stored middle
    assert (date(2026, 1, 10), date(2026, 1, 20)) not in src.calls
    assert (date(2026, 1, 1), date(2026, 1, 10)) in src.calls
    assert (date(2026, 1, 20), date(2026, 1, 31)) in src.calls


def test_separate_cached_ranges_do_not_hide_interior_holes(e):
    source = FakeSource()
    from insider_scanner.core.prices import repository
    from insider_scanner.persistence.coverage import DateInterval

    for start, end in [
        (date(2015, 1, 1), date(2015, 1, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
    ]:
        repository.store_fetch(
            e,
            "AAPL",
            "fake",
            DateInterval(start, end),
            source.fetch_daily("AAPL", start, end),
            now=datetime(2026, 11, 15, tzinfo=UTC),
            complete=True,
        )
    source.calls.clear()
    bars = get_price_history(
        "AAPL", date(2018, 1, 1), date(2020, 1, 1), source=source, engine=e
    )
    assert bars
    assert source.calls


def test_valid_empty_ranges_are_cached_without_inventing_bars(e):
    source = FakeSource()
    source.fetch_daily = lambda symbol, start, end: (
        source.calls.append((start, end)) or []
    )
    for _ in range(2):
        assert (
            get_price_history(
                "AAPL", date(2010, 1, 1), date(2010, 1, 3), source=source, engine=e
            )
            == []
        )
    assert source.calls == [(date(2010, 1, 1), date(2010, 1, 3))]


def test_legacy_and_other_provider_bars_do_not_claim_active_coverage(e):
    from insider_scanner.core.prices import repository

    repository.upsert_bars(
        e, [PriceBar("AAPL", date(2026, 1, 1), 99, 99, 99, 99, 1)], source="unknown"
    )
    source = FakeSource()
    bars = get_price_history(
        "AAPL", date(2026, 1, 1), date(2026, 1, 1), source=source, engine=e
    )
    assert source.calls == [(date(2026, 1, 1), date(2026, 1, 1))]
    assert bars[0].close == 1


def test_switching_provider_never_returns_or_trusts_other_provider_cache(e):
    first, second = FakeSource(), FakeSource()
    second.name = "other"
    for source in [first, second, first]:
        source.calls.clear()
        get_price_history(
            "AAPL", date(2026, 1, 1), date(2026, 1, 31), source=source, engine=e
        )
        assert source.calls, "overwritten provider coverage must be invalidated"


def _set_clock(monkeypatch, instant):
    from insider_scanner.core.prices import service

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(service, "datetime", Clock, raising=False)


def test_unfinished_bar_is_rechecked_after_short_freshness_window(e, monkeypatch):
    source = FakeSource()
    day = date(2026, 1, 5)
    instant = datetime(2026, 1, 5, 15, tzinfo=UTC)
    _set_clock(monkeypatch, instant)
    get_price_history("AAPL", day, day, source=source, engine=e)
    _set_clock(monkeypatch, instant + timedelta(minutes=6))
    get_price_history("AAPL", day, day, source=source, engine=e)
    assert source.calls == [(day, day), (day, day)]


def test_empty_current_day_is_briefly_reused_then_rechecked_next_day(e, monkeypatch):
    source = FakeSource()
    source.fetch_daily = lambda symbol, start, end: (
        source.calls.append((start, end)) or []
    )
    day = date(2026, 1, 5)
    instant = datetime(2026, 1, 5, 15, tzinfo=UTC)
    _set_clock(monkeypatch, instant)
    for _ in range(2):
        get_price_history("AAPL", day, day, source=source, engine=e)
    assert source.calls == [(day, day)]
    _set_clock(monkeypatch, instant + timedelta(days=1))
    get_price_history("AAPL", day, day, source=source, engine=e)
    assert source.calls == [(day, day), (day, day)]


@pytest.mark.parametrize("changed", ["close", "adjusted_close"])
def test_overlap_adjustment_change_refreshes_all_cached_history(e, changed):
    class AdjustingSource:
        name = "adjusting"
        factor = 1.0

        def __init__(self):
            self.calls = []

        def fetch_daily(self, symbol, start, end):
            self.calls.append((start, end))
            return [
                PriceBar(
                    symbol,
                    start + timedelta(days=n),
                    10 * self.factor if changed == "close" else 10,
                    10 * self.factor if changed == "close" else 10,
                    10 * self.factor if changed == "close" else 10,
                    10 * self.factor if changed == "close" else 10,
                    1,
                    10 * self.factor,
                )
                for n in range((end - start).days + 1)
            ]

    source = AdjustingSource()
    start, middle, end = date(2026, 1, 1), date(2026, 1, 4), date(2026, 1, 6)
    get_price_history("AAPL", start, middle, source=source, engine=e)
    source.factor = 0.5
    bars = get_price_history("AAPL", middle, end, source=source, engine=e)
    assert (start, end) in source.calls
    from insider_scanner.core.prices.repository import get_bars

    assert all(bar.adjusted_close == 5 for bar in get_bars(e, "AAPL", start, end))
    assert all(bar.adjusted_close == 5 for bar in bars)


def test_failed_refresh_keeps_cached_data_and_does_not_mark_coverage(e):
    from insider_scanner.core.prices.source import PriceSourceError

    source = FakeSource()
    start, end = date(2026, 1, 1), date(2026, 1, 4)
    get_price_history("AAPL", start, start, source=source, engine=e)

    def fail(*args):
        raise PriceSourceError("fake")

    source.fetch_daily = fail
    bars = get_price_history("AAPL", start, end, source=source, engine=e)
    assert [bar.date for bar in bars] == [start]
    source.fetch_daily = FakeSource.fetch_daily.__get__(source)
    source.calls.clear()
    get_price_history("AAPL", start, end, source=source, engine=e)
    assert source.calls


def test_intraday_close_changes_do_not_refetch_completed_history(e, monkeypatch):
    source = FakeSource()
    day = date(2026, 1, 5)
    instant = datetime(2026, 1, 5, 15, tzinfo=UTC)
    _set_clock(monkeypatch, instant)
    get_price_history("AAPL", date(2026, 1, 1), day, source=source, engine=e)
    source.calls.clear()

    def refresh(symbol, start, end):
        source.calls.append((start, end))
        return [
            PriceBar(symbol, start, 1, 1, 1, 1, 1),
            PriceBar(symbol, day, 2, 2, 2, 2, 1),
        ]

    source.fetch_daily = refresh
    _set_clock(monkeypatch, instant + timedelta(minutes=6))
    bars = get_price_history("AAPL", date(2026, 1, 1), day, source=source, engine=e)
    assert bars[-1].close == 2
    assert len(source.calls) == 1, (
        "an unfinished bar changing is not a corporate action"
    )


@pytest.mark.parametrize("mode", ["failure", "incomplete", "truncated"])
def test_failed_adjustment_replacement_preserves_every_old_bar(e, mode):
    from insider_scanner.core.prices.source import FetchedPriceBars, PriceSourceError

    source = FakeSource()
    first, last, end = date(2026, 1, 1), date(2026, 1, 4), date(2026, 1, 6)
    original = get_price_history("AAPL", first, last, source=source, engine=e)

    def refresh(symbol, start, stop):
        if start == first:
            if mode == "failure":
                raise PriceSourceError("fake", reason="network")
            return FetchedPriceBars(
                [PriceBar(symbol, last, 0.5, 0.5, 0.5, 0.5, 1)],
                complete=mode != "incomplete",
            )
        return [
            PriceBar(symbol, last, 0.5, 0.5, 0.5, 0.5, 1),
            PriceBar(symbol, end, 0.5, 0.5, 0.5, 0.5, 1),
        ]

    source.fetch_daily = refresh
    assert get_price_history("AAPL", first, end, source=source, engine=e) == original


def test_partial_provider_results_never_claim_coverage(e):
    from insider_scanner.core.prices.source import FetchedPriceBars, PriceSourceError

    source = FakeSource()
    day = date(2026, 1, 1)
    source.fetch_daily = lambda *args: FetchedPriceBars([], complete=False)
    with pytest.raises(PriceSourceError, match="schema"):
        get_price_history("AAPL", day, day, source=source, engine=e)
    source.fetch_daily = lambda *args: FetchedPriceBars(
        [PriceBar("AAPL", day, 1, 1, 1, 1, 1)], complete=False
    )
    assert get_price_history("AAPL", day, day, source=source, engine=e)
    source.calls.clear()
    source.fetch_daily = FakeSource.fetch_daily.__get__(source)
    get_price_history("AAPL", day, day, source=source, engine=e)
    assert source.calls
