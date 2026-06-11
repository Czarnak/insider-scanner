from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pytest
import requests

from insider_scanner.core.prices import (
    PriceBar,
    PriceSourceError,
    YahooSource,
    parse_yahoo_chart,
)


def yahoo_payload() -> dict[str, Any]:
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "timestamp": [86_399, 86_400],
                    "indicators": {
                        "quote": [
                            {
                                "open": [10, 20],
                                "high": [12, 23],
                                "low": [9, 19],
                                "close": [11, 22],
                                "volume": [100, 200],
                            }
                        ],
                        "adjclose": [{"adjclose": [10.5, None]}],
                    },
                }
            ],
        }
    }


def assert_sanitized(error: PriceSourceError, secret: str = "secret-payload") -> None:
    assert str(error) == "Yahoo price request failed"
    assert secret not in f"{error!s} {error!r} {error.args!r}"


def test_parse_yahoo_chart_returns_ordered_utc_bars() -> None:
    bars = parse_yahoo_chart(yahoo_payload(), " aapl ")

    assert bars == [
        PriceBar(
            symbol="AAPL",
            date=date(1970, 1, 1),
            open=10,
            high=12,
            low=9,
            close=11,
            volume=100,
            adjusted_close=10.5,
        ),
        PriceBar(
            symbol="AAPL",
            date=date(1970, 1, 2),
            open=20,
            high=23,
            low=19,
            close=22,
            volume=200,
            adjusted_close=None,
        ),
    ]


def test_parse_yahoo_chart_rejects_fractional_timestamp() -> None:
    payload = yahoo_payload()
    payload["chart"]["result"][0]["timestamp"][1] = 86_400.5

    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"chart": {"error": {"code": "Unauthorized", "description": "secret-payload"}}},
        {"chart": {"error": None}},
        {"chart": {"error": None, "result": []}},
        {"chart": {"error": None, "result": None}},
    ],
)
def test_parse_yahoo_chart_rejects_provider_errors_and_absent_results(
    payload: object,
) -> None:
    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"chart": []},
        {"chart": {"error": None, "result": {}}},
        {"chart": {"error": None, "result": [None]}},
        {"chart": {"error": None, "result": [{}, {}]}},
    ],
)
def test_parse_yahoo_chart_rejects_malformed_container_types(payload: object) -> None:
    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize("timestamps", [None, [], "86400", [None]])
def test_parse_yahoo_chart_rejects_missing_or_malformed_bars(
    timestamps: object,
) -> None:
    payload = yahoo_payload()
    payload["chart"]["result"][0]["timestamp"] = timestamps

    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_parse_yahoo_chart_rejects_mismatched_required_arrays(field: str) -> None:
    payload = yahoo_payload()
    payload["chart"]["result"][0]["indicators"]["quote"][0][field] = [1]

    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


def test_parse_yahoo_chart_rejects_mismatched_adjusted_close_array() -> None:
    payload = yahoo_payload()
    payload["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"] = [10.5]

    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_parse_yahoo_chart_rejects_null_required_values(field: str) -> None:
    payload = yahoo_payload()
    payload["chart"]["result"][0]["indicators"]["quote"][0][field][0] = None

    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    "indicators",
    [
        None,
        [],
        {},
        {"quote": {}},
        {"quote": []},
        {"quote": [None]},
        {"quote": [{"open": [10, 20]}]},
    ],
)
def test_parse_yahoo_chart_rejects_malformed_quote_structures(
    indicators: object,
) -> None:
    payload = yahoo_payload()
    payload["chart"]["result"][0]["indicators"] = indicators

    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    "adjclose",
    [
        {},
        [],
        [None],
        [{"adjclose": None}],
        [{"adjclose": "10.5"}],
    ],
)
def test_parse_yahoo_chart_rejects_malformed_adjusted_close_structures(
    adjclose: object,
) -> None:
    payload = yahoo_payload()
    payload["chart"]["result"][0]["indicators"]["adjclose"] = adjclose

    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("timestamp",), "not-a-timestamp"),
        (("open",), -1),
        (("high",), 8),
        (("low",), 12),
        (("close",), float("nan")),
        (("volume",), True),
        (("adjclose",), -1),
    ],
)
def test_parse_yahoo_chart_translates_invalid_bar_values(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = yahoo_payload()
    result = payload["chart"]["result"][0]
    if path[0] == "timestamp":
        result["timestamp"][0] = value
    elif path[0] == "adjclose":
        result["indicators"]["adjclose"][0]["adjclose"][0] = value
    else:
        result["indicators"]["quote"][0][path[0]][0] = value

    with pytest.raises(PriceSourceError) as raised:
        parse_yahoo_chart(payload, "AAPL")

    assert_sanitized(raised.value)


class FakeResponse:
    def __init__(
        self,
        *,
        payload: object = None,
        status_code: int = 200,
        content: bytes = b'{"chart": {}}',
        content_type: str = "application/json; charset=utf-8",
        json_error: BaseException | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return deepcopy(self._payload)


class FakeSession:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response or FakeResponse(payload=yahoo_payload())
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.headers = {}

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def utc_epoch(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=timezone.utc).timestamp())


def test_yahoo_source_sends_exact_chart_request_and_parses_response() -> None:
    session = FakeSession()
    source = YahooSource(session=session)
    start = date(2026, 1, 1)
    end = date(2026, 1, 31)

    bars = source.fetch_daily("^gspc", start, end)

    assert [bar.symbol for bar in bars] == ["^GSPC", "^GSPC"]
    assert session.calls == [
        (
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC",
            {
                "params": {
                    "interval": "1d",
                    "events": "history",
                    "period1": utc_epoch(start),
                    "period2": utc_epoch(end + timedelta(days=1)),
                },
                "timeout": (5.0, 20.0),
            },
        )
    ]


@pytest.mark.parametrize(
    ("symbol", "start", "end"),
    [
        ("AAPL/USD", date(2026, 1, 1), date(2026, 1, 2)),
        ("AAPL", "2026-01-01", date(2026, 1, 2)),
        ("AAPL", date(2026, 1, 2), date(2026, 1, 1)),
    ],
)
def test_yahoo_source_validates_before_network(
    symbol: object,
    start: object,
    end: object,
) -> None:
    session = FakeSession()
    source = YahooSource(session=session)

    with pytest.raises((TypeError, ValueError)):
        source.fetch_daily(symbol, start, end)

    assert session.calls == []


def test_yahoo_source_rejects_date_max_before_network() -> None:
    session = FakeSession()
    source = YahooSource(session=session)

    with pytest.raises(ValueError, match="end date"):
        source.fetch_daily("AAPL", date.max, date.max)

    assert session.calls == []


def test_yahoo_source_uses_a_new_requests_session_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = FakeSession()
    monkeypatch.setattr(requests, "Session", lambda: created)

    source = YahooSource()
    source.fetch_daily("AAPL", date(2026, 1, 1), date(2026, 1, 2))

    assert len(created.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        requests.Timeout("secret-payload"),
        requests.ConnectionError("secret-payload"),
        requests.RequestException("secret-payload"),
    ],
)
def test_yahoo_source_sanitizes_network_errors(error: BaseException) -> None:
    source = YahooSource(session=FakeSession(error=error))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 1, 1), date(2026, 1, 2))

    assert_sanitized(raised.value)


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
def test_yahoo_source_sanitizes_http_errors(status_code: int) -> None:
    response = FakeResponse(
        payload={"secret": "secret-payload"},
        status_code=status_code,
        content=b"secret-payload",
    )
    source = YahooSource(session=FakeSession(response=response))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 1, 1), date(2026, 1, 2))

    assert_sanitized(raised.value)


def test_yahoo_source_sanitizes_invalid_json() -> None:
    response = FakeResponse(
        content=b"secret-payload",
        json_error=requests.exceptions.JSONDecodeError(
            "secret-payload",
            "secret-payload",
            0,
        ),
    )
    source = YahooSource(session=FakeSession(response=response))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 1, 1), date(2026, 1, 2))

    assert_sanitized(raised.value)


def test_yahoo_source_does_not_mask_unexpected_response_errors() -> None:
    class BrokenResponse:
        pass

    source = YahooSource(session=FakeSession(response=BrokenResponse()))

    with pytest.raises(AttributeError):
        source.fetch_daily("AAPL", date(2026, 1, 1), date(2026, 1, 2))


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"", "application/json"),
        (b"   ", "application/json"),
        (b"<html>secret-payload</html>", "text/html"),
        (b"<!DOCTYPE html><title>secret-payload</title>", "application/json"),
        (b'{"chart": {}}', "text/html; charset=utf-8"),
    ],
)
def test_yahoo_source_rejects_empty_or_html_challenge_responses(
    content: bytes,
    content_type: str,
) -> None:
    response = FakeResponse(
        payload=yahoo_payload(),
        content=content,
        content_type=content_type,
    )
    source = YahooSource(session=FakeSession(response=response))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 1, 1), date(2026, 1, 2))

    assert_sanitized(raised.value)


def test_yahoo_source_preserves_parser_price_source_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = PriceSourceError("Yahoo")

    def fail_parser(payload: object, symbol: str) -> list[PriceBar]:
        del payload, symbol
        raise sentinel

    monkeypatch.setattr(
        "insider_scanner.core.prices.yahoo.parse_yahoo_chart",
        fail_parser,
    )
    source = YahooSource(session=FakeSession())

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 1, 1), date(2026, 1, 2))

    assert raised.value is sentinel
