from __future__ import annotations

from copy import deepcopy
from datetime import date
from json import JSONDecodeError
from typing import Any

import pytest
import requests

from insider_scanner.core.prices import (
    PriceBar,
    PriceSourceError,
    TiingoSource,
    parse_tiingo_json,
)

API_KEY = "tiingo-super-secret"
CONTROL_API_KEYS = [
    pytest.param(f"prefix{chr(code)}suffix", id=f"ascii-{code:02x}")
    for code in range(0x20)
] + [
    pytest.param("prefix\x7fsuffix", id="ascii-7f"),
    pytest.param("prefix\r\nInjected: value", id="embedded-crlf"),
]


def tiingo_payload() -> list[dict[str, Any]]:
    return [
        {
            "date": "2026-06-09T00:00:00.000Z",
            "open": 10,
            "high": 12,
            "low": 9,
            "close": 11,
            "volume": 100,
            "adjClose": 10.5,
        },
        {
            "date": "2026-06-10",
            "open": 20,
            "high": 23,
            "low": 19,
            "close": 22,
            "volume": 200,
            "adjClose": None,
        },
        {
            "date": "2026-06-11T00:00:00+00:00",
            "open": 30,
            "high": 34,
            "low": 29,
            "close": 33,
            "volume": 300,
        },
    ]


def assert_sanitized(
    error: PriceSourceError,
    secret: str = API_KEY,
) -> None:
    assert str(error) == "Tiingo price request failed"
    assert secret not in f"{error!s} {error!r} {error.args!r}"


def test_parse_tiingo_json_preserves_provider_order_and_date_forms() -> None:
    bars = parse_tiingo_json(tiingo_payload(), " aapl ")

    assert bars == [
        PriceBar(
            symbol="AAPL",
            date=date(2026, 6, 9),
            open=10,
            high=12,
            low=9,
            close=11,
            volume=100,
            adjusted_close=10.5,
        ),
        PriceBar(
            symbol="AAPL",
            date=date(2026, 6, 10),
            open=20,
            high=23,
            low=19,
            close=22,
            volume=200,
            adjusted_close=None,
        ),
        PriceBar(
            symbol="AAPL",
            date=date(2026, 6, 11),
            open=30,
            high=34,
            low=29,
            close=33,
            volume=300,
            adjusted_close=None,
        ),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "secret-payload",
        42,
        [],
        {"message": "secret-payload"},
        {"detail": "secret-payload"},
    ],
)
def test_parse_tiingo_json_rejects_invalid_top_level(payload: object) -> None:
    with pytest.raises(PriceSourceError) as raised:
        parse_tiingo_json(payload, "AAPL")

    assert_sanitized(raised.value, "secret-payload")


@pytest.mark.parametrize("record", [None, [], "record", 42])
def test_parse_tiingo_json_rejects_non_mapping_records(record: object) -> None:
    with pytest.raises(PriceSourceError) as raised:
        parse_tiingo_json([record], "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    "field",
    ["date", "open", "high", "low", "close", "volume"],
)
@pytest.mark.parametrize("mode", ["missing", "null"])
def test_parse_tiingo_json_rejects_missing_or_null_required_fields(
    field: str,
    mode: str,
) -> None:
    payload = tiingo_payload()[:1]
    if mode == "missing":
        del payload[0][field]
    else:
        payload[0][field] = None

    with pytest.raises(PriceSourceError) as raised:
        parse_tiingo_json(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        20260609,
        "",
        "not-a-date",
        "2026-02-30",
        "2026-06",
        "2026-06-09 trailing",
        "2026-06-09Tnot-a-time",
    ],
)
def test_parse_tiingo_json_rejects_invalid_dates(value: object) -> None:
    payload = tiingo_payload()[:1]
    payload[0]["date"] = value

    with pytest.raises(PriceSourceError) as raised:
        parse_tiingo_json(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    "value",
    [
        "2026-06-09T00:00:01Z",
        "2026-06-09T15:30:00+00:00",
        "2026-06-09T00:00:00+02:00",
        "2026-06-09T00:00:00-05:00",
        "2026-06-09T00:00:00",
    ],
)
def test_parse_tiingo_json_rejects_non_eod_utc_dates(value: str) -> None:
    payload = tiingo_payload()[:1]
    payload[0]["date"] = value

    with pytest.raises(PriceSourceError) as raised:
        parse_tiingo_json(payload, "AAPL")

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", "10"),
        ("open", -1),
        ("high", 8),
        ("low", 12),
        ("close", float("nan")),
        ("volume", True),
        ("adjClose", -1),
    ],
)
def test_parse_tiingo_json_translates_invalid_price_bar_values(
    field: str,
    value: object,
) -> None:
    payload = tiingo_payload()[:1]
    payload[0][field] = value

    with pytest.raises(PriceSourceError) as raised:
        parse_tiingo_json(payload, "AAPL")

    assert_sanitized(raised.value)


class FakeResponse:
    def __init__(
        self,
        *,
        payload: object = None,
        status_code: int = 200,
        content: bytes = b'[{"date": "2026-06-09"}]',
        content_type: str = "application/json; charset=utf-8",
        json_error: BaseException | None = None,
    ) -> None:
        self._payload = tiingo_payload() if payload is None else payload
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
        self.response = response or FakeResponse()
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def test_tiingo_source_sends_exact_request_and_parses_response() -> None:
    session = FakeSession()
    source = TiingoSource(API_KEY, session=session)
    start = date(2026, 6, 1)
    end = date(2026, 6, 9)

    bars = source.fetch_daily("^gspc", start, end)

    assert [bar.symbol for bar in bars] == ["^GSPC", "^GSPC", "^GSPC"]
    assert session.calls == [
        (
            "https://api.tiingo.com/tiingo/daily/%5EGSPC/prices",
            {
                "params": {
                    "startDate": "2026-06-01",
                    "endDate": "2026-06-09",
                    "resampleFreq": "daily",
                },
                "headers": {
                    "Authorization": f"Token {API_KEY}",
                    "Accept": "application/json",
                },
                "timeout": (5.0, 20.0),
            },
        )
    ]
    url, kwargs = session.calls[0]
    assert API_KEY not in url
    assert API_KEY not in repr(kwargs["params"])


@pytest.mark.parametrize("api_key", [None, "", " ", "\t\r\n"])
def test_tiingo_source_rejects_missing_or_blank_api_key(api_key: object) -> None:
    session = FakeSession()

    with pytest.raises((TypeError, ValueError)) as raised:
        TiingoSource(api_key, session=session)

    assert session.calls == []
    assert API_KEY not in f"{raised.value!s} {raised.value!r} {raised.value.args!r}"


@pytest.mark.parametrize("api_key", CONTROL_API_KEYS)
def test_tiingo_source_rejects_api_key_control_characters_before_session_creation(
    api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_creations = 0

    def create_session() -> FakeSession:
        nonlocal session_creations
        session_creations += 1
        return FakeSession()

    monkeypatch.setattr(requests, "Session", create_session)

    with pytest.raises(ValueError) as raised:
        TiingoSource(api_key)

    assert session_creations == 0
    assert api_key not in f"{raised.value!s} {raised.value!r} {raised.value.args!r}"


@pytest.mark.parametrize(
    ("symbol", "start", "end"),
    [
        ("AAPL/USD", date(2026, 6, 1), date(2026, 6, 9)),
        ("AAPL", "2026-06-01", date(2026, 6, 9)),
        ("AAPL", date(2026, 6, 10), date(2026, 6, 9)),
    ],
)
def test_tiingo_source_validates_inputs_before_network(
    symbol: object,
    start: object,
    end: object,
) -> None:
    session = FakeSession()
    source = TiingoSource(API_KEY, session=session)

    with pytest.raises((TypeError, ValueError)):
        source.fetch_daily(symbol, start, end)

    assert session.calls == []


def test_tiingo_source_uses_a_new_requests_session_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = FakeSession()
    monkeypatch.setattr(requests, "Session", lambda: created)

    source = TiingoSource(API_KEY)
    source.fetch_daily("AAPL", date(2026, 6, 1), date(2026, 6, 9))

    assert len(created.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        requests.Timeout(API_KEY),
        requests.ConnectionError(API_KEY),
        requests.RequestException(API_KEY),
    ],
)
def test_tiingo_source_sanitizes_network_errors(error: BaseException) -> None:
    source = TiingoSource(API_KEY, session=FakeSession(error=error))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 6, 1), date(2026, 6, 9))

    assert_sanitized(raised.value)


@pytest.mark.parametrize("status_code", [401, 403, 429, 400, 404, 500, 503])
def test_tiingo_source_sanitizes_http_errors(status_code: int) -> None:
    response = FakeResponse(
        payload={"message": API_KEY},
        status_code=status_code,
        content=API_KEY.encode(),
    )
    source = TiingoSource(API_KEY, session=FakeSession(response=response))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 6, 1), date(2026, 6, 9))

    assert_sanitized(raised.value)


@pytest.mark.parametrize(
    "json_error",
    [
        requests.exceptions.JSONDecodeError(API_KEY, API_KEY, 0),
        JSONDecodeError(API_KEY, API_KEY, 0),
        ValueError(API_KEY),
    ],
)
def test_tiingo_source_sanitizes_invalid_json(json_error: ValueError) -> None:
    response = FakeResponse(
        content=API_KEY.encode(),
        json_error=json_error,
    )
    source = TiingoSource(API_KEY, session=FakeSession(response=response))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 6, 1), date(2026, 6, 9))

    assert_sanitized(raised.value)


def test_tiingo_source_does_not_mask_unexpected_json_errors() -> None:
    sentinel = AttributeError(API_KEY)
    response = FakeResponse(json_error=sentinel)
    source = TiingoSource(API_KEY, session=FakeSession(response=response))

    with pytest.raises(AttributeError) as raised:
        source.fetch_daily("AAPL", date(2026, 6, 1), date(2026, 6, 9))

    assert raised.value is sentinel


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"", "application/json"),
        (b"   ", "application/json"),
        (b"<html>secret</html>", "text/html"),
        (b"<!DOCTYPE html><title>secret</title>", "application/json"),
        (b"[]", "text/html; charset=utf-8"),
    ],
)
def test_tiingo_source_rejects_empty_or_html_responses(
    content: bytes,
    content_type: str,
) -> None:
    response = FakeResponse(content=content, content_type=content_type)
    source = TiingoSource(API_KEY, session=FakeSession(response=response))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 6, 1), date(2026, 6, 9))

    assert_sanitized(raised.value)


def test_tiingo_source_sanitizes_parser_errors_containing_key() -> None:
    response = FakeResponse(
        payload={"message": API_KEY},
        content=API_KEY.encode(),
    )
    source = TiingoSource(API_KEY, session=FakeSession(response=response))

    with pytest.raises(PriceSourceError) as raised:
        source.fetch_daily("AAPL", date(2026, 6, 1), date(2026, 6, 9))

    assert_sanitized(raised.value)


def test_tiingo_source_does_not_mask_unexpected_response_errors() -> None:
    class BrokenResponse:
        pass

    source = TiingoSource(API_KEY, session=FakeSession(response=BrokenResponse()))

    with pytest.raises(AttributeError):
        source.fetch_daily("AAPL", date(2026, 6, 1), date(2026, 6, 9))
