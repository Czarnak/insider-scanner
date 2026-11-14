"""Reusable HTTP sessions and the shared SEC access boundary."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock, Thread, current_thread
from typing import Self
from urllib.parse import urlsplit

import requests

from insider_scanner.core.sec_client import RequestsSecTransport, SecClient
from insider_scanner.core.sec_fair_access import RateLimiter
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceLimits,
    SecResourceProfile,
)
from insider_scanner.utils.caching import cache_key, get_cached, set_cached
from insider_scanner.utils.config import DEFAULT_CACHE_TTL, get_sec_user_agent
from insider_scanner.utils.logging import get_logger

log = get_logger("http")
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        try:
            return float(value)
        except OverflowError:
            return None
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        return max(0.0, (target - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


class HttpSession:
    """One thread's pool with bounded GET retries and injectable ownership.

    Responses are consumed and released to the pool before returning. Retry
    waits are capped at 30 seconds; SEC cooldowns use SecClient's shared circuit.
    """

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        user_agent: str = "InsiderScanner/2.0 (research tool)",
        min_interval_seconds: float = 0.0,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._owned = session is None
        self._session = session if session is not None else requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._limiter = rate_limiter or RateLimiter(min_interval_seconds)
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._closed = False

    def get(
        self,
        url: str,
        *,
        timeout: float = 15,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        if self._closed:
            raise RuntimeError("HTTP session is closed")
        for attempt in range(1, self._max_attempts + 1):
            self._limiter.acquire()
            response = None
            retry_after = None
            try:
                response = self._session.get(
                    url, headers=headers, timeout=timeout, stream=True
                )
                retry_after = _retry_after(response.headers.get("Retry-After"))
                response.raise_for_status()
                _ = response.content
                return response
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else None
                transient = status in _RETRY_STATUSES or isinstance(
                    exc,
                    (
                        requests.ConnectionError,
                        requests.Timeout,
                        requests.exceptions.ChunkedEncodingError,
                    ),
                )
                if not transient or attempt == self._max_attempts:
                    raise
            finally:
                if response is not None:
                    response.close()
            delay = min(
                retry_after if retry_after is not None else 2 ** (attempt - 1), 30
            )
            if delay > 0:
                self._sleep(delay)
        raise AssertionError("unreachable retry state")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owned:
                self._session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# Requests sessions stay within their worker thread. Application shutdown owns
# the registry and releases all pools after workers have stopped.
_sessions_lock = Lock()
_sessions: dict[Thread, HttpSession] = {}
_sec_transports: dict[Thread, RequestsSecTransport] = {}


def _http_session() -> HttpSession:
    with _sessions_lock:
        thread = current_thread()
        if thread not in _sessions:
            _sessions[thread] = HttpSession()
        return _sessions[thread]


def _sec_transport() -> RequestsSecTransport:
    with _sessions_lock:
        thread = current_thread()
        if thread not in _sec_transports:
            _sec_transports[thread] = RequestsSecTransport()
        return _sec_transports[thread]


def close_http_sessions() -> None:
    """Close registered pools after workers stop; later calls create new pools."""
    with _sessions_lock:
        sessions: list[HttpSession | RequestsSecTransport] = [
            *_sessions.values(),
            *_sec_transports.values(),
        ]
        _sessions.clear()
        _sec_transports.clear()
    for session in sessions:
        session.close()


class _SecHeadersTransport:
    def __init__(self, headers: Mapping[str, str] | None) -> None:
        self._headers = dict(headers or {})

    def get(self, url: str, *, headers: Mapping[str, str], **kwargs):
        # The caller cannot replace the validated contact identity.
        extra = {k: v for k, v in self._headers.items() if k.lower() != "user-agent"}
        return _sec_transport().get(url, headers={**extra, **headers}, **kwargs)


def _fetch_sec_text(url: str, headers: Mapping[str, str] | None, timeout: float) -> str:
    policy = DEFAULT_SEC_SECURITY_POLICY
    limits = dict(policy.resource_limits)
    profile = SecResourceProfile.JSON_METADATA
    if not urlsplit(url).path.endswith(".json"):
        profile = SecResourceProfile.FILING_DOCUMENT
        limits[profile] = SecResourceLimits(
            limits[profile].allowed_media_types | {"text/html"},
            limits[profile].max_bytes,
        )
    client = SecClient(
        user_agent=get_sec_user_agent(),
        transport=_SecHeadersTransport(headers),
        policy=replace(
            policy, resource_limits=limits, request_timeout_seconds=(timeout, timeout)
        ),
    )
    return client.fetch_text(url, profile=profile)


def fetch_url(
    url: str,
    *,
    cache_dir: Path | None = None,
    cache_ttl: int = DEFAULT_CACHE_TTL,
    headers: dict | None = None,
    timeout: int = 15,
    use_sec_agent: bool = False,
) -> str:
    """Fetch cached text through reusable pools and bounded retries.

    Approved SEC hosts always use the validated SEC client, sharing its process
    limiter and blocking state even when the legacy flag is omitted.
    """
    key = cache_key(url)
    if cache_dir is not None:
        cached = get_cached(cache_dir, key, cache_ttl)
        if cached is not None:
            return cached
    host = urlsplit(url).hostname
    if use_sec_agent or host in DEFAULT_SEC_SECURITY_POLICY.allowed_hosts:
        text = _fetch_sec_text(url, headers, timeout)
    else:
        response = _http_session().get(url, headers=headers, timeout=timeout)
        if response.encoding is None:
            response.encoding = "utf-8"
        text = response.text
    if cache_dir is not None:
        set_cached(cache_dir, key, text, ttl=cache_ttl)
    return text
