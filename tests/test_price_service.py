from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.core.prices.service import get_price_history
from insider_scanner.persistence.schema import metadata


def make_engine() -> Engine:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return engine


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


def test_first_call_fetches_full_range_and_caches():
    e = make_engine()
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


def test_widening_range_fetches_only_edges():
    e = make_engine()
    src = FakeSource()
    get_price_history(
        "AAPL", date(2026, 1, 10), date(2026, 1, 20), source=src, engine=e
    )
    src.calls.clear()
    get_price_history("AAPL", date(2026, 1, 1), date(2026, 1, 31), source=src, engine=e)
    # only the two edge gaps, never the already-stored middle
    assert (date(2026, 1, 10), date(2026, 1, 20)) not in src.calls
    assert (date(2026, 1, 1), date(2026, 1, 9)) in src.calls
    assert (date(2026, 1, 21), date(2026, 1, 31)) in src.calls
