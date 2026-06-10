from datetime import date, datetime

import pytest

from insider_scanner.core.prices import PriceBar, PriceSource, PriceSourceError
from insider_scanner.core.prices.source import validate_price_request


class CompleteSource:
    def fetch_daily(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        return []


class IncompleteSource:
    pass


def test_price_source_supports_runtime_protocol_checks() -> None:
    assert isinstance(CompleteSource(), PriceSource)
    assert not isinstance(IncompleteSource(), PriceSource)


def test_boundary_validation_returns_normalized_symbol() -> None:
    result = validate_price_request(
        " brk-b ",
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert result == "BRK-B"


@pytest.mark.parametrize("symbol", ["", "  ", "AAPL/USD", None])
def test_boundary_validation_rejects_invalid_symbols(symbol: object) -> None:
    with pytest.raises((TypeError, ValueError), match="symbol"):
        validate_price_request(symbol, date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-01-01", date(2026, 1, 31)),
        (date(2026, 1, 1), datetime(2026, 1, 31)),
    ],
)
def test_boundary_validation_rejects_non_date_values(
    start: object,
    end: object,
) -> None:
    with pytest.raises(TypeError, match="date"):
        validate_price_request("AAPL", start, end)


def test_boundary_validation_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="start"):
        validate_price_request("AAPL", date(2026, 2, 1), date(2026, 1, 31))


def test_price_source_error_does_not_expose_context_or_cause_secrets() -> None:
    secret = "api-token=super-secret"
    error = PriceSourceError(
        "alpha-vantage",
        context={"endpoint": "/daily", "api_key": secret},
        cause=RuntimeError(secret),
    )

    rendered = f"{error!s} {error!r} {error.args!r}"
    assert "alpha-vantage" in str(error)
    assert secret not in rendered
    assert "api_key" not in rendered
