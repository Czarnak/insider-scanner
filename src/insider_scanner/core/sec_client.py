"""Bounded, injectable HTTP client for approved SEC resources."""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import BinaryIO, Protocol, Self
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from insider_scanner.core.sec_fair_access import (
    DEFAULT_SEC_RETRY_POLICY,
    RateLimiter,
    SecCircuitBreaker,
    SecCircuitOpenError,
    SecRetryPolicy,
    backoff_delay,
    default_circuit_breaker,
    default_rate_limiter,
    no_jitter,
)
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceLimits,
    SecResourceProfile,
    SecSecurityPolicy,
    SecSecurityReason,
)
from insider_scanner.utils.atomic_file import atomic_binary_writer

_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"(?P<email>[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63})"
    r"(?![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
)
_PLACEHOLDER_EMAIL = "contact@example.com"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_LOGGER = logging.getLogger(__name__)
_RETRYABLE_TRANSPORT_ERRORS = (
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)


class _SecResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def iter_content(self, chunk_size: int) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class SecTransport(Protocol):
    """Structural interface for an SEC streaming HTTP transport."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: tuple[float, float],
        allow_redirects: bool,
        stream: bool,
    ) -> _SecResponse: ...


@dataclass(frozen=True, slots=True)
class RequestsSecTransport:
    """Own a reusable connection pool for one SEC client or worker."""

    session: requests.Session = field(default_factory=requests.Session)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Retry at the client boundary, where each attempt is rate limited and
        # includes the streamed body. Adapter-level retries bypass that gate.
        adapter = requests.adapters.HTTPAdapter(max_retries=0)
        self.session.mount("https://", adapter)

    def close(self) -> None:
        if not self._closed:
            object.__setattr__(self, "_closed", True)
            self.session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: tuple[float, float],
        allow_redirects: bool,
        stream: bool,
    ) -> requests.Response:
        if self._closed:
            raise RuntimeError("SEC transport is closed")
        return self.session.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
            stream=stream,
        )


class SecClientError(Exception):
    """Base class for safe SEC client failures."""


class SecConfigurationError(SecClientError):
    """Raised when client configuration is invalid."""


class SecClientSecurityError(SecClientError):
    """Raised when an SEC request violates the immutable security policy."""

    def __init__(self, reason: SecSecurityReason, url: str) -> None:
        self.reason = reason
        self.url = (
            "<rejected-sec-url>"
            if reason in {SecSecurityReason.HOST, SecSecurityReason.REDIRECT}
            else _safe_url_label(url)
        )
        super().__init__(f"SEC request rejected ({reason.value}) for {self.url}")


class SecHttpError(SecClientError):
    """Raised when an approved SEC host returns a non-success status."""

    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = _safe_url_label(url)
        super().__init__(f"SEC request returned HTTP {status_code} for {self.url}")


class SecFilingNotFoundError(SecHttpError):
    """Raised for an HTTP 404 — a recoverable 'missing filing/index' signal.

    A subclass of :class:`SecHttpError` so existing ``except SecHttpError``
    handlers keep working; callers that want to treat a missing resource as a
    skippable, non-fatal outcome catch this narrower type.
    """


class SecTransportError(SecClientError):
    """Raised when the underlying transport cannot complete a request."""

    def __init__(self, url: str) -> None:
        self.url = _safe_url_label(url)
        super().__init__(f"SEC transport failed for {self.url}")


class SecBlockedError(SecClientError):
    """SEC access is paused process-wide after sustained blocking."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            "SEC access is blocked; stop this run and retry after "
            f"{math.ceil(retry_after_seconds)} seconds of cooldown"
        )


class SecDecodeError(SecClientError):
    """Raised when an SEC text response is not valid UTF-8."""

    def __init__(self, url: str) -> None:
        self.url = _safe_url_label(url)
        super().__init__(f"SEC response was not valid UTF-8 for {self.url}")


class SecDownloadCancelled(SecClientError):
    """A cooperative cancellation left the destination unpublished."""


@dataclass(frozen=True, slots=True)
class SecClient:
    """Fetch approved SEC resources with bounded, typed failures.

    Fair-access behavior is injectable: ``rate_limiter`` throttles every network
    hop below the SEC ceiling, ``retry_policy`` governs bounded retries of
    transient throttling responses, and ``sleep``/``jitter`` are injected so
    backoff is deterministic under test.
    """

    user_agent: str
    transport: SecTransport | None = None
    _owns_transport: bool = field(default=False, init=False, repr=False, compare=False)
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY
    rate_limiter: RateLimiter = field(default_factory=default_rate_limiter)
    circuit_breaker: SecCircuitBreaker = field(default_factory=default_circuit_breaker)
    retry_policy: SecRetryPolicy = DEFAULT_SEC_RETRY_POLICY
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[float], float] = no_jitter

    def __post_init__(self) -> None:
        _validate_user_agent(self.user_agent)
        if not isinstance(self.policy, SecSecurityPolicy):
            raise SecConfigurationError("SEC security policy is invalid")
        if not isinstance(self.retry_policy, SecRetryPolicy):
            raise SecConfigurationError("SEC retry policy is invalid")
        if self.transport is None:
            object.__setattr__(self, "transport", RequestsSecTransport())
            object.__setattr__(self, "_owns_transport", True)

    def close(self) -> None:
        """Close an internally created transport; injected transports stay owned by callers."""
        if self._owns_transport and isinstance(self.transport, RequestsSecTransport):
            self.transport.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def fetch_text(self, url: str, *, profile: SecResourceProfile) -> str:
        content = self.fetch_bytes(url, profile=profile)
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SecDecodeError(url) from None

    def fetch_bytes(self, url: str, *, profile: SecResourceProfile) -> bytes:
        return self._fetch(url, profile=profile)

    def fetch_to_file(
        self,
        url: str,
        path: Path,
        *,
        profile: SecResourceProfile,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        """Stream bounded retries to a sibling file and publish it atomically."""
        _validate_url(url, self.policy)
        _check_cancelled(cancelled)
        with atomic_binary_writer(path) as output:
            self._fetch(url, profile=profile, output=output, cancelled=cancelled)
        return path

    def _fetch(
        self,
        url: str,
        *,
        profile: SecResourceProfile,
        output: BinaryIO | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> bytes:
        limits = self.policy.limits_for(profile)
        current_url = _validate_url(url, self.policy)
        redirects = 0
        while True:
            result = self._request_with_retry(
                current_url, limits, output=output, cancelled=cancelled
            )
            if isinstance(result, bytes):
                return result
            response = result
            try:
                if response.status_code in _REDIRECT_STATUSES:
                    current_url = _redirect_target(
                        response, current_url, redirects, self.policy
                    )
                    redirects += 1
                    continue
            finally:
                _close_response(response, current_url)

    def _request_with_retry(
        self,
        url: str,
        limits: SecResourceLimits,
        *,
        output: BinaryIO | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> _SecResponse | bytes:
        """Retry each complete network hop, including its streamed response body."""
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            _check_cancelled(cancelled)
            if output is not None:
                output.seek(0)
                output.truncate()
            self.rate_limiter.acquire()
            _check_cancelled(cancelled)
            try:
                ticket = self.circuit_breaker.acquire()
            except SecCircuitOpenError as exc:
                raise SecBlockedError(exc.retry_after_seconds) from None
            retry_after = None
            try:
                response = self._send(url)
                status = response.status_code
                retry_after = _parse_retry_after(
                    _header(response.headers, "Retry-After")
                )
                if status in _REDIRECT_STATUSES:
                    self.circuit_breaker.record_response(ticket, status)
                    return response
                try:
                    if 200 <= status < 300:
                        _validate_response_headers(response.headers, limits, url)
                        content = _read_bounded(
                            response,
                            limits,
                            self.policy,
                            url,
                            output=output,
                            cancelled=cancelled,
                        )
                        self.circuit_breaker.record_response(ticket, status)
                        return content
                    cooldown = self.circuit_breaker.record_response(
                        ticket, status, retry_after
                    )
                    if cooldown is not None:
                        raise SecBlockedError(cooldown)
                finally:
                    _close_response(response, url)
            except SecTransportError:
                self.circuit_breaker.record_transport_failure(ticket)
                if attempt == self.retry_policy.max_attempts:
                    raise
                self._sleep_backoff(attempt, None)
                continue
            except BaseException:
                self.circuit_breaker.record_transport_failure(ticket)
                raise
            if status == 404:
                raise SecFilingNotFoundError(status, url)
            if (
                status in self.retry_policy.retry_statuses
                and attempt < self.retry_policy.max_attempts
            ):
                self._sleep_backoff(attempt, retry_after)
                continue
            raise SecHttpError(status, url)
        raise AssertionError("unreachable retry loop")

    def _sleep_backoff(self, attempt: int, retry_after: float | None) -> None:
        delay = backoff_delay(
            self.retry_policy, attempt, retry_after=retry_after, jitter=self.jitter
        )
        if delay > 0:
            self.sleep(delay)

    def _send(self, url: str) -> _SecResponse:
        assert self.transport is not None
        try:
            return self.transport.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.policy.request_timeout_seconds,
                allow_redirects=False,
                stream=True,
            )
        except _RETRYABLE_TRANSPORT_ERRORS as exc:
            raise SecTransportError(url) from exc


def _redirect_target(
    response: _SecResponse,
    current_url: str,
    redirects: int,
    policy: SecSecurityPolicy,
) -> str:
    if redirects >= policy.max_redirects:
        raise SecClientSecurityError(SecSecurityReason.REDIRECT, current_url)
    location = _header(response.headers, "Location")
    if location is None or not location.strip():
        raise SecClientSecurityError(SecSecurityReason.REDIRECT, current_url)
    candidate = urljoin(current_url, location)
    try:
        return _validate_url(candidate, policy)
    except SecClientSecurityError:
        raise SecClientSecurityError(SecSecurityReason.REDIRECT, candidate) from None


def _validate_response_headers(
    headers: Mapping[str, str], limits: SecResourceLimits, url: str
) -> None:
    content_type = _header(headers, "Content-Type")
    if content_type is None:
        raise SecClientSecurityError(SecSecurityReason.CONTENT_TYPE, url)
    normalized = content_type.split(";", 1)[0].strip().casefold()
    if normalized not in limits.allowed_media_types:
        raise SecClientSecurityError(SecSecurityReason.CONTENT_TYPE, url)
    content_length = _header(headers, "Content-Length")
    if content_length is None:
        return
    if not content_length.isascii() or not content_length.isdigit():
        raise SecClientSecurityError(SecSecurityReason.RESPONSE_SIZE, url)
    if int(content_length) > limits.max_bytes:
        raise SecClientSecurityError(SecSecurityReason.RESPONSE_SIZE, url)


def _read_bounded(
    response: _SecResponse,
    limits: SecResourceLimits,
    policy: SecSecurityPolicy,
    url: str,
    *,
    output: BinaryIO | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(policy.response_chunk_bytes):
            _check_cancelled(cancelled)
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise SecTransportError(url)
            total += len(chunk)
            if total > limits.max_bytes:
                raise SecClientSecurityError(SecSecurityReason.RESPONSE_SIZE, url)
            if output is None:
                chunks.append(chunk)
            else:
                output.write(chunk)
        _check_cancelled(cancelled)
    except SecClientError:
        raise
    except _RETRYABLE_TRANSPORT_ERRORS as exc:
        raise SecTransportError(url) from exc
    return b"".join(chunks)


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise SecDownloadCancelled("SEC download cancelled; destination is unchanged")


def _validate_url(url: str, policy: SecSecurityPolicy) -> str:
    if not isinstance(url, str) or not url or any(char.isspace() for char in url):
        raise SecClientSecurityError(SecSecurityReason.HOST, str(url))
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SecClientSecurityError(SecSecurityReason.HOST, url) from exc
    if (
        parsed.scheme.casefold() != "https"
        or host not in policy.allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise SecClientSecurityError(SecSecurityReason.HOST, url)
    return url


def _safe_url_label(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return "<invalid-sec-url>"
    if parsed.scheme.casefold() != "https" or host is None:
        return "<invalid-sec-url>"
    netloc = host if port in (None, 443) else f"{host}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value if isinstance(value, str) else None
    return None


def _parse_retry_after(value: str | None) -> float | None:
    """Parse nonnegative delta-seconds or an RFC HTTP-date for retry/cooldown."""
    if value is None:
        return None
    candidate = value.strip()
    if candidate.isascii() and candidate.isdigit():
        try:
            seconds = float(candidate)
        except OverflowError:
            return None
        return seconds if math.isfinite(seconds) else None
    try:
        timestamp = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return None
    if timestamp.tzinfo is None:
        return None
    return max(0.0, (timestamp - datetime.now(UTC)).total_seconds())


def _close_response(response: _SecResponse, url: str) -> None:
    try:
        response.close()
    except Exception:  # noqa: BLE001 - closing must not mask the validated outcome
        _LOGGER.warning(
            "SEC diagnostic stage=response_close reason=close_failed url=%s",
            _safe_url_label(url),
        )


def _validate_user_agent(user_agent: str) -> None:
    if not isinstance(user_agent, str) or not user_agent.strip():
        raise SecConfigurationError("SEC User-Agent must be a non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in user_agent):
        raise SecConfigurationError("SEC User-Agent contains invalid characters")
    email_match = _EMAIL_PATTERN.search(user_agent)
    if email_match is None:
        raise SecConfigurationError("SEC User-Agent must contain a valid contact email")
    if email_match.group("email").casefold() == _PLACEHOLDER_EMAIL:
        raise SecConfigurationError("SEC User-Agent must not use a placeholder email")
    identity = user_agent[: email_match.start()] + user_agent[email_match.end() :]
    if not any(character.isalnum() for character in identity):
        raise SecConfigurationError(
            "SEC User-Agent must contain an app or company identity"
        )
