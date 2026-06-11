"""Yahoo Finance daily price provider."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, time, timedelta, timezone
from typing import Mapping
from urllib.parse import quote

import requests

from .model import PriceBar
from .source import PriceSourceError, validate_price_request

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_CONNECT_TIMEOUT_SECONDS = 5.0
_READ_TIMEOUT_SECONDS = 20.0
_TIMEOUT = (_CONNECT_TIMEOUT_SECONDS, _READ_TIMEOUT_SECONDS)
_REQUIRED_FIELDS = ("open", "high", "low", "close", "volume")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError
    return value


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError
    return value


def _utc_date(timestamp: object) -> Date:
    if type(timestamp) is not int:
        raise TypeError
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()


def parse_yahoo_chart(payload: object, symbol: str) -> list[PriceBar]:
    """Parse a decoded Yahoo chart response into ordered daily bars."""
    try:
        chart = _mapping(_mapping(payload)["chart"])
        if chart["error"] is not None:
            raise PriceSourceError("Yahoo")

        results = _array(chart["result"])
        if not results:
            raise ValueError
        result = _mapping(results[0])
        timestamps = _array(result["timestamp"])
        if not timestamps:
            raise ValueError

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

        return [
            PriceBar(
                symbol=symbol,
                date=_utc_date(timestamp),
                open=required["open"][index],
                high=required["high"][index],
                low=required["low"][index],
                close=required["close"][index],
                volume=required["volume"][index],
                adjusted_close=adjusted_values[index],
            )
            for index, timestamp in enumerate(timestamps)
        ]
    except PriceSourceError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, OSError):
        raise PriceSourceError("Yahoo") from None


def _epoch(value: Date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=timezone.utc).timestamp())


class YahooSource:
    """Fetch validated daily bars from Yahoo Finance's chart endpoint."""

    def __init__(self, session: requests.Session | None = None) -> None:
        if session is not None:
            self._session = session
        else:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
                }
            )

    def fetch_daily(self, symbol: str, start: Date, end: Date) -> list[PriceBar]:
        """Fetch daily bars for an inclusive UTC date range."""
        normalized_symbol = validate_price_request(symbol, start, end)
        if end == Date.max:
            raise ValueError("end date must allow an exclusive upper bound")

        try:
            response = self._session.get(
                f"{_CHART_URL}/{quote(normalized_symbol, safe='')}",
                params={
                    "interval": "1d",
                    "events": "history",
                    "period1": _epoch(start),
                    "period2": _epoch(end + timedelta(days=1)),
                },
                timeout=_TIMEOUT,
            )
            if response.status_code >= 400:
                raise PriceSourceError("Yahoo")

            content = response.content
            content_type = response.headers.get("Content-Type", "").lower()
            stripped_content = content.lstrip().lower()
            if (
                not stripped_content
                or "html" in content_type
                or stripped_content.startswith((b"<html", b"<!doctype html"))
            ):
                raise PriceSourceError("Yahoo")

            payload = response.json()
            return parse_yahoo_chart(payload, normalized_symbol)
        except PriceSourceError:
            raise
        except requests.RequestException:
            raise PriceSourceError("Yahoo") from None
