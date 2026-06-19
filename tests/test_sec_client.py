"""Behavior tests for the typed SEC HTTP client."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
import math
from typing import cast
from unittest.mock import Mock, patch

import pytest

from insider_scanner.core.sec_client import (
    RequestsSecTransport,
    SecClient,
    SecClientError,
    SecConfigurationError,
    SecDecodeError,
    SecHttpError,
    SecTransport,
    SecTransportError,
)


VALID_USER_AGENT = "Insider Scanner ops@insider-scanner.example"
VALID_URL = "https://www.sec.gov/files/company_tickers.json"


@dataclass(frozen=True, slots=True)
class StubResponse:
    status_code: int
    content: bytes


def configured_transport(
    *, status_code: int = 200, content: bytes = b"payload"
) -> Mock:
    transport = Mock(spec=SecTransport)
    transport.get.return_value = StubResponse(status_code, content)
    return transport


def make_client(transport: Mock, *, timeout: float = 15.0) -> SecClient:
    return SecClient(
        user_agent=VALID_USER_AGENT,
        transport=cast(SecTransport, transport),
        timeout_seconds=timeout,
    )


def test_sec_client_is_frozen_and_slotted() -> None:
    client = make_client(configured_transport())

    assert not hasattr(client, "__dict__")
    with pytest.raises(FrozenInstanceError):
        client.timeout_seconds = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid_user_agent",
    [
        None,
        42,
        "",
        "   ",
        "contact@example.com",
        "CONTACT@EXAMPLE.COM",
        "Insider Scanner",
        "ops@insider-scanner.example",
        "Insider Scanner invalid-email",
        "Insider Scanner ops@example",
        "Insider Scanner\r\nops@example.org",
    ],
)
def test_invalid_or_placeholder_identity_fails_before_transport(
    invalid_user_agent: object,
) -> None:
    transport = configured_transport()

    with pytest.raises(SecConfigurationError):
        SecClient(
            user_agent=cast(str, invalid_user_agent),
            transport=cast(SecTransport, transport),
        )

    transport.get.assert_not_called()


@pytest.mark.parametrize(
    "invalid_timeout",
    [None, True, "15", 0.0, -1.0, math.inf, -math.inf, math.nan],
)
def test_invalid_timeout_fails_before_transport(invalid_timeout: object) -> None:
    transport = configured_transport()

    with pytest.raises(SecConfigurationError):
        make_client(transport, timeout=cast(float, invalid_timeout))

    transport.get.assert_not_called()


@pytest.mark.parametrize(
    "invalid_url",
    [
        "",
        "   ",
        "company_tickers.json",
        "http://www.sec.gov/files/company_tickers.json",
        "https:///files/company_tickers.json",
        "https://www.sec.gov/bad path",
    ],
)
def test_invalid_url_fails_before_transport(invalid_url: str) -> None:
    transport = configured_transport()
    client = make_client(transport)

    with pytest.raises(SecConfigurationError):
        client.fetch_bytes(invalid_url)

    transport.get.assert_not_called()


def test_injected_transport_receives_user_agent_and_timeout() -> None:
    transport = configured_transport(content=b"document")
    client = make_client(transport, timeout=7.5)

    result = client.fetch_bytes(VALID_URL)

    assert result == b"document"
    transport.get.assert_called_once_with(
        VALID_URL,
        headers={"User-Agent": VALID_USER_AGENT},
        timeout=7.5,
    )


def test_default_transport_is_stateless_requests_adapter() -> None:
    response = StubResponse(status_code=200, content=b"document")

    with patch(
        "insider_scanner.core.sec_client.requests.get", return_value=response
    ) as requests_get:
        client = SecClient(user_agent=VALID_USER_AGENT)
        result = client.fetch_bytes(VALID_URL)

    assert isinstance(client.transport, RequestsSecTransport)
    assert not hasattr(client.transport, "__dict__")
    assert result == b"document"
    requests_get.assert_called_once_with(
        VALID_URL,
        headers={"User-Agent": VALID_USER_AGENT},
        timeout=15.0,
    )


def test_fetch_text_decodes_utf8_with_bom() -> None:
    transport = configured_transport(content=b"\xef\xbb\xbfSEC filing \xe2\x82\xac")

    result = make_client(transport).fetch_text(VALID_URL)

    assert result == "SEC filing \u20ac"


@pytest.mark.parametrize("status_code", [200, 201, 204, 206, 299])
def test_every_2xx_status_is_successful(status_code: int) -> None:
    transport = configured_transport(status_code=status_code, content=b"ok")

    assert make_client(transport).fetch_bytes(VALID_URL) == b"ok"


@pytest.mark.parametrize("status_code", [199, 300, 404, 500])
def test_non_2xx_raises_typed_error_without_response_content(
    status_code: int,
) -> None:
    secret_body = b"private filing response body"
    transport = configured_transport(status_code=status_code, content=secret_body)

    with pytest.raises(SecHttpError) as exc_info:
        make_client(transport).fetch_bytes(VALID_URL)

    error = exc_info.value
    assert error.status_code == status_code
    assert error.url == VALID_URL
    assert VALID_URL in str(error)
    assert str(status_code) in str(error)
    assert secret_body.decode() not in str(error)
    assert secret_body.decode() not in repr(error)


def test_transport_exception_is_safely_wrapped_and_chained() -> None:
    transport = configured_transport()
    cause = RuntimeError("private transport details")
    transport.get.side_effect = cause

    with pytest.raises(SecTransportError) as exc_info:
        make_client(transport).fetch_bytes(VALID_URL)

    error = exc_info.value
    assert error.url == VALID_URL
    assert error.__cause__ is cause
    assert "private transport details" not in str(error)
    assert "private transport details" not in repr(error)


def test_invalid_utf8_raises_typed_decode_error() -> None:
    transport = configured_transport(content=b"\xffprivate response")

    with pytest.raises(SecDecodeError) as exc_info:
        make_client(transport).fetch_text(VALID_URL)

    error = exc_info.value
    assert error.url == VALID_URL
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "private response" not in str(error)
    assert "private response" not in repr(error)


def test_error_types_share_sec_client_base() -> None:
    for error_type in (
        SecConfigurationError,
        SecHttpError,
        SecTransportError,
        SecDecodeError,
    ):
        assert issubclass(error_type, SecClientError)
