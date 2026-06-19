"""Typed, injectable HTTP client for SEC resources."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Mapping, Protocol
from urllib.parse import urlsplit

import requests


_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"(?P<email>[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63})"
    r"(?![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
)
_PLACEHOLDER_EMAIL = "contact@example.com"


class _SecResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def content(self) -> bytes: ...


class SecTransport(Protocol):
    """Structural interface for an SEC HTTP transport."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> _SecResponse: ...


@dataclass(frozen=True, slots=True)
class RequestsSecTransport:
    """Stateless ``requests.get`` transport adapter."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> requests.Response:
        return requests.get(url, headers=headers, timeout=timeout)


class SecClientError(Exception):
    """Base class for safe SEC client failures."""


class SecConfigurationError(SecClientError):
    """Raised when client configuration or a request URL is invalid."""


class SecHttpError(SecClientError):
    """Raised when the SEC returns a non-success status."""

    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"SEC request returned HTTP {status_code} for {url}")


class SecTransportError(SecClientError):
    """Raised when the underlying transport cannot complete a request."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"SEC transport failed for {url}")


class SecDecodeError(SecClientError):
    """Raised when an SEC response is not valid UTF-8 text."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"SEC response was not valid UTF-8 for {url}")


@dataclass(frozen=True, slots=True)
class SecClient:
    """Fetch SEC resources with validated configuration and typed failures."""

    user_agent: str
    transport: SecTransport = field(default_factory=RequestsSecTransport)
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        _validate_user_agent(self.user_agent)
        _validate_timeout(self.timeout_seconds)

    def fetch_text(self, url: str) -> str:
        """Fetch and decode UTF-8 text, accepting an optional BOM."""
        content = self.fetch_bytes(url)
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass

        # Raise outside the handler so raw response bytes are not retained.
        raise SecDecodeError(url)

    def fetch_bytes(self, url: str) -> bytes:
        """Fetch raw bytes from a validated HTTPS URL."""
        _validate_url(url)
        try:
            response = self.transport.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise SecTransportError(url) from exc

        if not 200 <= response.status_code < 300:
            raise SecHttpError(response.status_code, url)
        return response.content


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
        raise SecConfigurationError("SEC User-Agent must contain an app or company identity")


def _validate_timeout(timeout_seconds: float) -> None:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise SecConfigurationError("SEC timeout must be finite and greater than zero")


def _validate_url(url: str) -> None:
    if not isinstance(url, str) or not url or any(char.isspace() for char in url):
        raise SecConfigurationError("SEC URL must be a non-empty HTTPS URL")

    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise SecConfigurationError("SEC URL is invalid") from exc

    if parsed.scheme.casefold() != "https" or not host:
        raise SecConfigurationError("SEC URL must use HTTPS and include a host")
