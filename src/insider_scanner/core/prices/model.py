"""Validated price data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from math import isfinite
import re

_SYMBOL_PATTERN = re.compile(r"[A-Z0-9^][A-Z0-9._^-]{0,31}", re.ASCII)


def normalize_symbol(symbol: object) -> str:
    """Return a normalized provider symbol or raise for invalid input."""
    if not isinstance(symbol, str):
        raise TypeError("symbol must be a string")

    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol must not be empty")
    if _SYMBOL_PATTERN.fullmatch(normalized) is None:
        raise ValueError("symbol contains unsupported characters")
    return normalized


def _numeric_value(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if normalized < 0:
        raise ValueError(f"{name} must be nonnegative")
    return normalized


@dataclass(frozen=True)
class PriceBar:
    """Immutable daily OHLCV price data."""

    symbol: str
    date: Date
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjusted_close: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if type(self.date) is not Date:
            raise TypeError("date must be a date")

        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, _numeric_value(name, getattr(self, name)))
        if self.adjusted_close is not None:
            adjusted = _numeric_value("adjusted close", self.adjusted_close)
            object.__setattr__(self, "adjusted_close", adjusted)
        self._validate_range()

    def _validate_range(self) -> None:
        if self.high < self.low:
            raise ValueError("high must not be below low")

        for name in ("open", "close"):
            value = getattr(self, name)
            if not self.low <= value <= self.high:
                raise ValueError(f"{name.replace('_', ' ')} must lie within low/high")

    @property
    def plot_close(self) -> float:
        """Return adjusted close when available, otherwise close."""
        if self.adjusted_close is not None:
            return self.adjusted_close
        return self.close
