from dataclasses import FrozenInstanceError
from datetime import date, datetime
from math import inf, nan

import pytest

from insider_scanner.core.prices import PriceBar


def make_bar(**overrides: object) -> PriceBar:
    values = {
        "symbol": " aapl ",
        "date": date(2026, 6, 9),
        "open": 100,
        "high": 110,
        "low": 90,
        "close": 105,
        "volume": 1_000,
        "adjusted_close": 104,
    }
    return PriceBar(**(values | overrides))


def test_price_bar_is_immutable_and_normalizes_values() -> None:
    bar = make_bar()

    assert bar.symbol == "AAPL"
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 110.0, 90.0, 105.0)
    assert bar.volume == 1_000.0
    assert bar.adjusted_close == 104.0
    with pytest.raises(FrozenInstanceError):
        bar.close = 101.0


@pytest.mark.parametrize("symbol", ["", "   ", "AAPL/USD", "AAPL!", 42])
def test_price_bar_rejects_invalid_symbol(symbol: object) -> None:
    with pytest.raises((TypeError, ValueError), match="symbol"):
        make_bar(symbol=symbol)


@pytest.mark.parametrize("bar_date", ["2026-06-09", datetime(2026, 6, 9)])
def test_price_bar_rejects_non_date_values(bar_date: object) -> None:
    with pytest.raises(TypeError, match="date"):
        make_bar(date=bar_date)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", True),
        ("high", "110"),
        ("low", -1),
        ("close", nan),
        ("volume", inf),
        ("adjusted_close", -1),
    ],
)
def test_price_bar_rejects_invalid_numeric_values(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError), match=field.replace("_", " ")):
        make_bar(**{field: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": 80},
        {"open": 111},
        {"close": 89},
    ],
)
def test_price_bar_rejects_incoherent_ranges(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_bar(**overrides)


def test_price_bar_accepts_adjusted_close_outside_raw_range() -> None:
    bar = make_bar(adjusted_close=55)

    assert bar.adjusted_close == 55.0


def test_plot_close_prefers_adjusted_close() -> None:
    assert make_bar(adjusted_close=103).plot_close == 103.0


def test_plot_close_falls_back_to_close() -> None:
    assert make_bar(adjusted_close=None).plot_close == 105.0
