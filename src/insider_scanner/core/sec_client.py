"""Bounded, injectable HTTP client for approved SEC resources."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from http.cookiejar import DefaultCookiePolicy
import logging
import re
import threading
import time
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from insider_scanner.core.sec_fair_access import (
    DEFAULT_SEC_RETRY_POLICY,
    RateLimiter,
    SecRetryPolicy,
    backoff_delay,
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


# Per-host keep-alive pool size. The bounded concurrent fetch in
# ``services.sec_downloads`` (DEFAULT_DOWNLOAD_WORKERS) issues several requests
# at once; sizing the pool to cover that avoids "connection pool is full"
# churn while staying modest against SEC hosts.
_SESSION_POOL_MAXSIZE = 16


def _build_pooled_session() -> requests.Session:
    """Return a session whose https adapter reuses pooled keep-alive connections.

    Connection reuse is the only state the session is allowed to keep: a
    block-all cookie policy preserves the prior stateless behavior so an SEC
    ``Set-Cookie`` is never stored or echoed back on a reused connection.
    """
    session = requests.Session()
    session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=4, pool_maxsize=_SESSION_POOL_MAXSIZE
    )
    session.mount("https://", adapter)
    return session


@dataclass(frozen=True, slots=True)
class RequestsSecTransport:
    """Streaming ``requests`` transport with per-thread pooled keep-alive sessions.

    Each thread lazily gets its own :class:`requests.Session` (a connection pool
    that reuses TCP/TLS connections to SEC hosts), so the bounded concurrent
    fetch pays one handshake per worker instead of one per request. Sessions are
    never shared across threads, so there is no shared session/cookie-jar state
    to race: per-request headers and timeout are passed on every call and the
    session object itself is never mutated. All trust decisions (host allowlist,
    redirect policy, size/content-type bounds) remain in :class:`SecClient`.
    """

    _local: threading.local = field(
        default_factory=threading.local, repr=False, compare=False
    )

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = _build_pooled_session()
            self._local.session = session
        return session

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: tuple[float, float],
        allow_redirects: bool,
        stream: bool,
    ) -> requests.Response:
        return self._session().get(
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


class SecDecodeError(SecClientError):
    """Raised when an SEC text response is not valid UTF-8."""

    def __init__(self, url: str) -> None:
        self.url = _safe_url_label(url)
        super().__init__(f"SEC response was not valid UTF-8 for {self.url}")


@dataclass(frozen=True, slots=True)
class SecClient:
    """Fetch approved SEC resources with bounded, typed failures.

    Fair-access behavior is injectable: ``rate_limiter`` throttles every network
    hop below the SEC ceiling, ``retry_policy`` governs bounded retries of
    transient throttling responses, and ``sleep``/``jitter`` are injected so
    backoff is deterministic under test.
    """

    user_agent: str
    transport: SecTransport = field(default_factory=RequestsSecTransport)
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY
    rate_limiter: RateLimiter = field(default_factory=default_rate_limiter)
    retry_policy: SecRetryPolicy = DEFAULT_SEC_RETRY_POLICY
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[float], float] = no_jitter

    def __post_init__(self) -> None:
        _validate_user_agent(self.user_agent)
        if not isinstance(self.policy, SecSecurityPolicy):
            raise SecConfigurationError("SEC security policy is invalid")
        if not isinstance(self.retry_policy, SecRetryPolicy):
            raise SecConfigurationError("SEC retry policy is invalid")

    def fetch_text(self, url: str, *, profile: SecResourceProfile) -> str:
        content = self.fetch_bytes(url, profile=profile)
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SecDecodeError(url) from None

    def fetch_bytes(self, url: str, *, profile: SecResourceProfile) -> bytes:
        limits = self.policy.limits_for(profile)
        current_url = _validate_url(url, self.policy)
        redirects = 0
        while True:
            response = self._request_with_retry(current_url)
            try:
                if response.status_code in _REDIRECT_STATUSES:
                    current_url = _redirect_target(
                        response, current_url, redirects, self.policy
                    )
                    redirects += 1
                    continue
                _validate_response_headers(response.headers, limits, current_url)
                return _read_bounded(response, limits, self.policy, current_url)
            finally:
                _close_response(response, current_url)

    def _request_with_retry(self, url: str) -> _SecResponse:
        """Return a redirect or 2xx response, retrying transient throttling.

        Error statuses are handled here: 404 raises the recoverable
        :class:`SecFilingNotFoundError`; statuses in ``retry_policy`` are retried
        with bounded backoff; all other non-success statuses raise
        :class:`SecHttpError`. Transport failures are not retried.
        """
        attempt = 0
        while True:
            attempt += 1
            self.rate_limiter.acquire()
            response = self._send(url)
            status = response.status_code
            if status in _REDIRECT_STATUSES or 200 <= status < 300:
                return response
            retry_after = (
                _parse_retry_after(_header(response.headers, "Retry-After"))
                if status in self.retry_policy.retry_statuses
                else None
            )
            _close_response(response, url)
            if status == 404:
                raise SecFilingNotFoundError(status, url)
            if (
                status in self.retry_policy.retry_statuses
                and attempt < self.retry_policy.max_attempts
            ):
                self._sleep_backoff(attempt, retry_after)
                continue
            raise SecHttpError(status, url)

    def _sleep_backoff(self, attempt: int, retry_after: float | None) -> None:
        delay = backoff_delay(
            self.retry_policy, attempt, retry_after=retry_after, jitter=self.jitter
        )
        if delay > 0:
            self.sleep(delay)

    def _send(self, url: str) -> _SecResponse:
        try:
            return self.transport.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.policy.request_timeout_seconds,
                allow_redirects=False,
                stream=True,
            )
        except Exception as exc:
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
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(policy.response_chunk_bytes):
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise SecTransportError(url)
            total += len(chunk)
            if total > limits.max_bytes:
                raise SecClientSecurityError(SecSecurityReason.RESPONSE_SIZE, url)
            chunks.append(chunk)
    except SecClientError:
        raise
    except Exception as exc:
        raise SecTransportError(url) from exc
    return b"".join(chunks)


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
    """Parse a ``Retry-After`` delta-seconds value, else fall back to backoff.

    Only non-negative ASCII integer seconds (RFC 7231 delta-seconds) are
    accepted. The HTTP-date form and fractional values fall back to computed
    exponential backoff rather than risking an unbounded or surprising wait.
    """
    if value is None:
        return None
    candidate = value.strip()
    if candidate.isascii() and candidate.isdigit():
        return float(candidate)
    return None


def _close_response(response: _SecResponse, url: str) -> None:
    try:
        response.close()
    except Exception:
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
