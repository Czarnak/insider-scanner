"""Behavior tests for the bounded SEC HTTP client."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError, dataclass, field, replace
from typing import cast
from unittest.mock import Mock, call, patch

import pytest

from insider_scanner.core.sec_client import (
    RequestsSecTransport,
    SecClient,
    SecClientError,
    SecClientSecurityError,
    SecConfigurationError,
    SecDecodeError,
    SecHttpError,
    SecTransport,
    SecTransportError,
)
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceLimits,
    SecResourceProfile,
    SecSecurityReason,
)


VALID_USER_AGENT = "Insider Scanner ops@insider-scanner.example"
VALID_URL = "https://www.sec.gov/files/company_tickers.json"
TEXT_PROFILE = SecResourceProfile.DAILY_INDEX


@dataclass(slots=True)
class StubResponse:
    status_code: int = 200
    chunks: tuple[bytes, ...] = (b"payload",)
    headers: Mapping[str, str] = field(
        default_factory=lambda: {"Content-Type": "text/plain"}
    )
    close_calls: int = 0
    iter_chunk_sizes: list[int] = field(default_factory=list)

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        self.iter_chunk_sizes.append(chunk_size)
        yield from self.chunks

    def close(self) -> None:
        self.close_calls += 1


class RaisingBodyResponse(StubResponse):
    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        raise RuntimeError("private streamed body details")
        yield b""  # pragma: no cover


class RaisingCloseResponse(StubResponse):
    def close(self) -> None:
        raise RuntimeError("private close details")


def configured_transport(*responses: StubResponse) -> Mock:
    transport = Mock(spec=SecTransport)
    configured = responses or (StubResponse(),)
    transport.get.side_effect = list(configured)
    return transport


def make_client(transport: Mock, *, policy=DEFAULT_SEC_SECURITY_POLICY) -> SecClient:
    return SecClient(
        user_agent=VALID_USER_AGENT,
        transport=cast(SecTransport, transport),
        policy=policy,
    )


def fetch_bytes(client: SecClient, url: str = VALID_URL) -> bytes:
    return client.fetch_bytes(url, profile=TEXT_PROFILE)


def test_sec_client_is_frozen_and_slotted() -> None:
    client = make_client(configured_transport())

    assert not hasattr(client, "__dict__")
    with pytest.raises(FrozenInstanceError):
        client.policy = DEFAULT_SEC_SECURITY_POLICY  # type: ignore[misc]


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
def test_invalid_identity_fails_before_transport(invalid_user_agent: object) -> None:
    transport = configured_transport()

    with pytest.raises(SecConfigurationError):
        SecClient(
            user_agent=cast(str, invalid_user_agent),
            transport=cast(SecTransport, transport),
        )

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
        "https://www.sec.gov.evil.example/file",
        "https://sec.gov/file",
        "https://www.sec.gov./file",
        "https://user@www.sec.gov/file",
        "https://www.sec.gov:444/file",
        "https://www.sec.gov/file#fragment",
    ],
)
def test_unsafe_url_fails_before_transport(invalid_url: str) -> None:
    transport = configured_transport()

    with pytest.raises(SecClientSecurityError) as exc_info:
        fetch_bytes(make_client(transport), invalid_url)

    assert exc_info.value.reason is SecSecurityReason.HOST
    transport.get.assert_not_called()


def test_injected_transport_receives_secure_request_options() -> None:
    response = StubResponse(chunks=(b"document",))
    transport = configured_transport(response)

    assert fetch_bytes(make_client(transport)) == b"document"
    transport.get.assert_called_once_with(
        VALID_URL,
        headers={"User-Agent": VALID_USER_AGENT},
        timeout=(15.0, 15.0),
        allow_redirects=False,
        stream=True,
    )
    assert response.iter_chunk_sizes == [64 * 1024]
    assert response.close_calls == 1


def test_default_transport_uses_requests_streaming_options() -> None:
    response = StubResponse(chunks=(b"document",))

    with patch(
        "insider_scanner.core.sec_client.requests.get", return_value=response
    ) as requests_get:
        client = SecClient(user_agent=VALID_USER_AGENT)
        result = fetch_bytes(client)

    assert isinstance(client.transport, RequestsSecTransport)
    assert not hasattr(client.transport, "__dict__")
    assert result == b"document"
    requests_get.assert_called_once_with(
        VALID_URL,
        headers={"User-Agent": VALID_USER_AGENT},
        timeout=(15.0, 15.0),
        allow_redirects=False,
        stream=True,
    )


def test_relative_sec_redirect_is_validated_then_followed() -> None:
    redirect = StubResponse(
        status_code=302,
        chunks=(),
        headers={"Location": "/Archives/filing.txt"},
    )
    final = StubResponse(chunks=(b"filing",))
    transport = configured_transport(redirect, final)

    result = fetch_bytes(make_client(transport))

    assert result == b"filing"
    assert transport.get.call_args_list == [
        call(
            VALID_URL,
            headers={"User-Agent": VALID_USER_AGENT},
            timeout=(15.0, 15.0),
            allow_redirects=False,
            stream=True,
        ),
        call(
            "https://www.sec.gov/Archives/filing.txt",
            headers={"User-Agent": VALID_USER_AGENT},
            timeout=(15.0, 15.0),
            allow_redirects=False,
            stream=True,
        ),
    ]
    assert redirect.close_calls == final.close_calls == 1


def test_external_redirect_is_rejected_before_second_request() -> None:
    redirect = StubResponse(
        status_code=302,
        chunks=(),
        headers={"Location": "https://evil.example/steal"},
    )
    transport = configured_transport(redirect)

    with pytest.raises(SecClientSecurityError) as exc_info:
        fetch_bytes(make_client(transport))

    assert exc_info.value.reason is SecSecurityReason.REDIRECT
    assert "evil.example" not in str(exc_info.value)
    assert "/steal" not in str(exc_info.value)
    assert transport.get.call_count == 1
    assert redirect.close_calls == 1


def test_fourth_redirect_is_rejected() -> None:
    redirects = tuple(
        StubResponse(status_code=302, chunks=(), headers={"Location": f"/r/{i}"})
        for i in range(4)
    )
    transport = configured_transport(*redirects)

    with pytest.raises(SecClientSecurityError) as exc_info:
        fetch_bytes(make_client(transport))

    assert exc_info.value.reason is SecSecurityReason.REDIRECT
    assert transport.get.call_count == 4
    assert all(response.close_calls == 1 for response in redirects)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Content-Type": "text/html"},
        {"Content-Type": "text/plain", "Content-Length": "-1"},
        {"Content-Type": "text/plain", "Content-Length": "1, 1"},
        {"Content-Type": "text/plain", "Content-Length": "not-a-number"},
    ],
)
def test_invalid_response_headers_fail_closed(headers: Mapping[str, str]) -> None:
    response = StubResponse(headers=headers)

    with pytest.raises(SecClientSecurityError):
        fetch_bytes(make_client(configured_transport(response)))

    assert response.close_calls == 1


def test_content_type_parameters_are_normalized() -> None:
    response = StubResponse(
        chunks=(b"index",), headers={"Content-Type": "Text/Plain; charset=utf-8"}
    )

    assert fetch_bytes(make_client(configured_transport(response))) == b"index"


def small_text_policy(max_bytes: int):
    limits = dict(DEFAULT_SEC_SECURITY_POLICY.resource_limits)
    limits[TEXT_PROFILE] = SecResourceLimits(frozenset({"text/plain"}), max_bytes)
    return replace(DEFAULT_SEC_SECURITY_POLICY, resource_limits=limits)


def test_declared_oversize_fails_before_body_iteration() -> None:
    response = StubResponse(
        chunks=(b"secret",),
        headers={"Content-Type": "text/plain", "Content-Length": "5"},
    )

    with pytest.raises(SecClientSecurityError) as exc_info:
        fetch_bytes(make_client(configured_transport(response), policy=small_text_policy(4)))

    assert exc_info.value.reason is SecSecurityReason.RESPONSE_SIZE
    assert response.iter_chunk_sizes == []
    assert response.close_calls == 1


def test_streamed_oversize_fails_and_closes_response() -> None:
    response = StubResponse(chunks=(b"123", b"45"))

    with pytest.raises(SecClientSecurityError) as exc_info:
        fetch_bytes(make_client(configured_transport(response), policy=small_text_policy(4)))

    assert exc_info.value.reason is SecSecurityReason.RESPONSE_SIZE
    assert response.close_calls == 1


def test_streaming_exception_is_safely_wrapped_and_response_is_closed() -> None:
    response = RaisingBodyResponse()

    with pytest.raises(SecTransportError) as exc_info:
        fetch_bytes(make_client(configured_transport(response)))

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "private streamed body details" not in str(exc_info.value)
    assert response.close_calls == 1


def test_close_failure_log_has_stage_reason_and_no_raw_error(caplog) -> None:
    response = RaisingCloseResponse(chunks=(b"ok",))

    assert fetch_bytes(make_client(configured_transport(response))) == b"ok"

    assert "stage=response_close" in caplog.text
    assert "reason=close_failed" in caplog.text
    assert "private close details" not in caplog.text


def test_empty_stream_chunks_do_not_affect_payload() -> None:
    response = StubResponse(chunks=(b"a", b"", b"b"))

    assert fetch_bytes(make_client(configured_transport(response))) == b"ab"
    assert response.close_calls == 1


@pytest.mark.parametrize("status_code", [200, 201, 204, 206, 299])
def test_every_2xx_status_is_successful(status_code: int) -> None:
    response = StubResponse(status_code=status_code, chunks=(b"ok",))

    assert fetch_bytes(make_client(configured_transport(response))) == b"ok"


@pytest.mark.parametrize("status_code", [199, 304, 404, 500])
def test_non_success_status_is_typed_and_payload_free(status_code: int) -> None:
    response = StubResponse(status_code=status_code, chunks=(b"private payload",))

    with pytest.raises(SecHttpError) as exc_info:
        fetch_bytes(make_client(configured_transport(response)))

    assert exc_info.value.status_code == status_code
    assert "private payload" not in str(exc_info.value)
    assert response.close_calls == 1


def test_transport_exception_is_safely_wrapped_and_chained() -> None:
    transport = configured_transport()
    cause = RuntimeError("private transport details")
    transport.get.side_effect = cause

    with pytest.raises(SecTransportError) as exc_info:
        fetch_bytes(make_client(transport), VALID_URL + "?secret=value")

    assert exc_info.value.__cause__ is cause
    assert "secret=value" not in str(exc_info.value)
    assert "private transport details" not in str(exc_info.value)


def test_fetch_text_decodes_utf8_with_bom() -> None:
    response = StubResponse(chunks=(b"\xef\xbb\xbfSEC filing \xe2\x82\xac",))

    result = make_client(configured_transport(response)).fetch_text(
        VALID_URL, profile=TEXT_PROFILE
    )

    assert result == "SEC filing \u20ac"


def test_invalid_utf8_raises_typed_payload_free_error() -> None:
    response = StubResponse(chunks=(b"\xffprivate response",))

    with pytest.raises(SecDecodeError) as exc_info:
        make_client(configured_transport(response)).fetch_text(
            VALID_URL, profile=TEXT_PROFILE
        )

    assert "private response" not in str(exc_info.value)
    assert response.close_calls == 1


def test_client_error_types_share_base() -> None:
    for error_type in (
        SecConfigurationError,
        SecClientSecurityError,
        SecHttpError,
        SecTransportError,
        SecDecodeError,
    ):
        assert issubclass(error_type, SecClientError)
