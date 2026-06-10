from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.core.prices import repository as repo
from insider_scanner.persistence.schema import metadata


def make_engine() -> Engine:
    engine = create_engine("sqlite://")  # in-memory
    metadata.create_all(engine)
    return engine


def test_bar_roundtrips_through_db():
    e = make_engine()
    bar = PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 100, adjusted_close=1.4)
    repo.upsert_bars(e, [bar], source="stooq")
    out = repo.get_bars(e, "AAPL", date(2026, 1, 1), date(2026, 1, 31))
    assert len(out) == 1
    assert out[0].close == 1.5
    assert out[0].adjusted_close == 1.4

def test_get_coverage_none_when_empty():
    e = make_engine()
    assert repo.get_coverage(e, "AAPL") is None


def test_get_coverage_returns_min_max():
    e = make_engine()
    repo.upsert_bars(e, [
        PriceBar("AAPL", date(2026, 1, 5), 1, 1, 1, 1, 1),
        PriceBar("AAPL", date(2026, 1, 9), 1, 1, 1, 1, 1),
    ])
    assert repo.get_coverage(e, "AAPL") == (date(2026, 1, 5), date(2026, 1, 9))


def test_find_missing_ranges_no_coverage_returns_full_range():
    rng = repo.find_missing_ranges(None, date(2026, 1, 1), date(2026, 1, 31))
    assert rng == [(date(2026, 1, 1), date(2026, 1, 31))]


def test_find_missing_ranges_extends_both_edges():
    cov = (date(2026, 1, 10), date(2026, 1, 20))
    rng = repo.find_missing_ranges(cov, date(2026, 1, 1), date(2026, 1, 31))
    assert rng == [
        (date(2026, 1, 1), date(2026, 1, 9)),
        (date(2026, 1, 21), date(2026, 1, 31)),
    ]


def test_find_missing_ranges_fully_covered_returns_empty():
    cov = (date(2026, 1, 1), date(2026, 1, 31))
    assert repo.find_missing_ranges(cov, date(2026, 1, 5), date(2026, 1, 20)) == []
