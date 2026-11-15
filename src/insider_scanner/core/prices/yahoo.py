"""Yahoo Finance daily price provider."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta, timezone, tzinfo
from datetime import date as Date
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from insider_scanner.utils.logging import get_logger

from .model import PriceBar
from .source import (
    FetchedPriceBars,
    PriceSourceError,
    _checked_json,
    validate_price_request,
)

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_CONNECT_TIMEOUT_SECONDS = 5.0
_READ_TIMEOUT_SECONDS = 20.0
_TIMEOUT = (_CONNECT_TIMEOUT_SECONDS, _READ_TIMEOUT_SECONDS)
_REQUIRED_FIELDS = ("open", "high", "low", "close", "volume")
log = get_logger("prices.yahoo")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError
    return value


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError
    return value


def _exchange_date(timestamp: object, exchange_zone: tzinfo) -> Date:
    if type(timestamp) is not int:
        raise TypeError
    return datetime.fromtimestamp(timestamp, tz=exchange_zone).date()


def _exchange_zone(result: Mapping[str, object]) -> tzinfo:
    meta = _mapping(result.get("meta", {}))
    zone_name = meta.get("exchangeTimezoneName")
    if isinstance(zone_name, str):
        try:
            return ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            pass
    offset = meta.get("gmtoffset", 0)
    if type(offset) is not int:
        raise TypeError
    return timezone(timedelta(seconds=offset))


def _empty_bars(result: Mapping[str, object]) -> FetchedPriceBars:
    indicators = _mapping(result["indicators"])
    _array(indicators["quote"])
    for name in ("quote", "adjclose"):
        for record in _array(indicators.get(name, [])):
            for values in _mapping(record).values():
                if _array(values):
                    raise ValueError
    return FetchedPriceBars()


def parse_yahoo_chart(payload: object, symbol: str) -> FetchedPriceBars:
    """Parse a decoded Yahoo chart response into ordered daily bars."""
    try:
        chart = _mapping(_mapping(payload)["chart"])
        if chart["error"] is not None:
            raise PriceSourceError("Yahoo")

        results = _array(chart["result"])
        if not results:
            raise ValueError
        result = _mapping(results[0])
        if result.get("timestamp") is None:
            # Successful non-trading/pre-IPO responses retain metadata and
            # empty indicator objects but can omit the timestamp member.
            _mapping(result["meta"])
            return _empty_bars(result)
        timestamps = _array(result["timestamp"])
        if not timestamps:
            return _empty_bars(result)
        exchange_zone = _exchange_zone(result)

        indicators = _mapping(result["indicators"])
        quotes = _array(indicators["quote"])
        if not quotes:
            raise ValueError
        quote_data = _mapping(quotes[0])
        required = {field: _array(quote_data[field]) for field in _REQUIRED_FIELDS}
        if any(len(values) != len(timestamps) for values in required.values()):
            raise ValueError

        adjusted_values: list[object] = [None] * len(timestamps)
        if "adjclose" in indicators:
            adjusted = _array(indicators["adjclose"])
            if not adjusted:
                raise ValueError
            adjusted_values = _array(_mapping(adjusted[0])["adjclose"])
            if len(adjusted_values) != len(timestamps):
                raise ValueError

        bars = FetchedPriceBars()
        skipped = 0
        for index, timestamp in enumerate(timestamps):
            try:
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        date=_exchange_date(timestamp, exchange_zone),
                        open=required["open"][index],
                        high=required["high"][index],
                        low=required["low"][index],
                        close=required["close"][index],
                        volume=required["volume"][index],
                        adjusted_close=adjusted_values[index],
                    )
                )
            except (TypeError, ValueError, OverflowError, OSError):
                skipped += 1
        if skipped:
            bars.complete = False
            log.debug("Skipped %d invalid Yahoo price rows", skipped)
        return bars
    except PriceSourceError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise PriceSourceError("Yahoo", reason="schema", cause=exc)


def _epoch(value: Date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp())


class YahooSource:
    """Fetch validated daily bars from Yahoo Finance's chart endpoint."""

    name = "yahoo"

    def __init__(self, session: requests.Session | None = None) -> None:
        self._owns_session = session is None
        if session is not None:
            self._session = session
        else:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
                }
            )

    def close(self) -> None:
        """Release a session created by this provider."""
        if self._owns_session:
            self._session.close()

    def fetch_daily(self, symbol: str, start: Date, end: Date) -> list[PriceBar]:
        """Fetch daily bars for inclusive exchange-local calendar dates."""
        normalized_symbol = validate_price_request(symbol, start, end)
        if end == Date.max:
            raise ValueError("end date must allow an exclusive upper bound")

        try:
            response = self._session.get(
                f"{_CHART_URL}/{quote(normalized_symbol, safe='')}",
                params={
                    "interval": "1d",
                    "events": "history",
                    # Exchange session opens can fall on either neighboring
                    # UTC date; clip padded data after converting its dates.
                    "period1": _epoch(start) - 86_400,
                    "period2": _epoch(end + timedelta(days=1)) + 86_400,
                },
                timeout=_TIMEOUT,
            )
            payload = _checked_json(response, "Yahoo")
            bars = parse_yahoo_chart(payload, normalized_symbol)
            return FetchedPriceBars(
                (bar for bar in bars if start <= bar.date <= end),
                complete=bars.complete,
            )
        except PriceSourceError:
            raise
        except requests.RequestException as exc:
            raise PriceSourceError("Yahoo", reason="network", cause=exc)
