"""Tiingo daily price provider."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta
import re
from typing import Mapping
from urllib.parse import quote

import requests

from .model import PriceBar
from .source import PriceSourceError, validate_price_request

_DAILY_URL = "https://api.tiingo.com/tiingo/daily"
_TIMEOUT = (5.0, 20.0)
_REQUIRED_FIELDS = ("date", "open", "high", "low", "close", "volume")
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def _record(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError
    return value


def _calendar_date(value: object) -> Date:
    if not isinstance(value, str):
        raise TypeError
    if len(value) == 10:
        if _DATE_PATTERN.fullmatch(value) is None:
            raise ValueError
        return Date.fromisoformat(value)
    if (
        len(value) <= 10
        or _DATE_PATTERN.fullmatch(value[:10]) is None
        or value[10] != "T"
        or not value.endswith(("Z", "+00:00"))
    ):
        raise ValueError

    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.utcoffset() != timedelta(0) or any(
        (parsed.hour, parsed.minute, parsed.second, parsed.microsecond)
    ):
        raise ValueError
    return parsed.date()


def parse_tiingo_json(payload: object, symbol: str) -> list[PriceBar]:
    """Parse decoded Tiingo EOD JSON into provider-ordered daily bars."""
    try:
        if not isinstance(payload, list) or not payload:
            raise TypeError

        bars: list[PriceBar] = []
        for item in payload:
            record = _record(item)
            if any(
                field not in record or record[field] is None
                for field in _REQUIRED_FIELDS
            ):
                raise ValueError
            bars.append(
                PriceBar(
                    symbol=symbol,
                    date=_calendar_date(record["date"]),
                    open=record["open"],
                    high=record["high"],
                    low=record["low"],
                    close=record["close"],
                    volume=record["volume"],
                    adjusted_close=record.get("adjClose"),
                )
            )
        return bars
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PriceSourceError("Tiingo") from None


class TiingoSource:
    """Fetch validated daily bars from Tiingo's EOD endpoint."""

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
    ) -> None:
        if not isinstance(api_key, str):
            raise TypeError("api_key must be a string")
        if any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in api_key
        ):
            raise ValueError("api_key contains invalid control characters")
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("api_key must not be blank")

        self._api_key = normalized_key
        self._session = session if session is not None else requests.Session()

    def fetch_daily(self, symbol: str, start: Date, end: Date) -> list[PriceBar]:
        """Fetch daily bars for an inclusive date range."""
        normalized_symbol = validate_price_request(symbol, start, end)

        try:
            response = self._session.get(
                (f"{_DAILY_URL}/{quote(normalized_symbol, safe='')}/prices"),
                params={
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "resampleFreq": "daily",
                },
                headers={
                    "Authorization": f"Token {self._api_key}",
                    "Accept": "application/json",
                },
                timeout=_TIMEOUT,
            )
            if response.status_code >= 400:
                raise PriceSourceError("Tiingo")

            content = response.content
            content_type = response.headers.get("Content-Type", "").lower()
            stripped_content = content.lstrip().lower()
            if (
                not stripped_content
                or "html" in content_type
                or stripped_content.startswith((b"<html", b"<!doctype html"))
            ):
                raise PriceSourceError("Tiingo")

            try:
                payload = response.json()
            except ValueError:
                raise PriceSourceError("Tiingo") from None
            return parse_tiingo_json(payload, normalized_symbol)
        except PriceSourceError:
            raise
        except requests.RequestException:
            raise PriceSourceError("Tiingo") from None
