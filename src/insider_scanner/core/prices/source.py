"""Price provider contracts and shared request validation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date as Date
from typing import Protocol, runtime_checkable

import requests

from .model import PriceBar, normalize_symbol

_PROVIDER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}", re.ASCII)


@runtime_checkable
class PriceSource(Protocol):
    """Provider capable of fetching validated daily price bars."""

    name: str

    def fetch_daily(self, symbol: str, start: Date, end: Date) -> list[PriceBar]:
        """Fetch daily bars for an inclusive date range."""
        ...


class FetchedPriceBars(list[PriceBar]):
    """Bars whose coverage is complete only when every supplied row is valid."""

    def __init__(self, bars: Iterable[PriceBar] = (), *, complete: bool = True) -> None:
        super().__init__(bars)
        self.complete = complete


class PriceSourceError(RuntimeError):
    """Sanitized price provider failure."""

    def __init__(
        self,
        provider: str,
        *,
        context: Mapping[str, object] | None = None,
        cause: BaseException | None = None,
        reason: str = "provider",
        status_code: int | None = None,
    ) -> None:
        del context
        self.provider = _sanitized_provider(provider)
        self.reason = reason if reason in _ERROR_REASONS else "provider"
        self.status_code = (
            status_code
            if type(status_code) is int and 100 <= status_code <= 599
            else None
        )
        self.cause_type = type(cause).__name__ if cause is not None else None
        details = self.reason
        if self.status_code is not None:
            details += f", HTTP {self.status_code}"
        super().__init__(f"{self.provider} price request failed ({details})")
        if self.cause_type is not None:
            # Request/decoder exceptions can include credentials, URLs or bodies.
            # Keep the failure category in the chain without propagating its text.
            self.__cause__ = RuntimeError(self.cause_type)


_ERROR_REASONS = frozenset(
    {"provider", "auth", "rate_limit", "http", "schema", "network", "json", "empty"}
)


def _sanitized_provider(provider: object) -> str:
    if isinstance(provider, str):
        candidate = provider.strip()
        if _PROVIDER_PATTERN.fullmatch(candidate) is not None:
            return candidate
    return "price provider"


def validate_price_request(symbol: object, start: object, end: object) -> str:
    """Validate provider boundary inputs and return the normalized symbol."""
    normalized_symbol = normalize_symbol(symbol)
    if type(start) is not Date or type(end) is not Date:
        raise TypeError("start and end must be date values")
    if start > end:
        raise ValueError("start must not be after end")
    return normalized_symbol


def _checked_json(response: requests.Response, provider: str) -> object:
    """Decode a nonempty provider response without exposing its body on failure."""
    if response.status_code >= 400:
        status = response.status_code
        reason = (
            "auth"
            if status in {401, 403}
            else "rate_limit"
            if status == 429
            else "http"
        )
        raise PriceSourceError(provider, reason=reason, status_code=status)

    content_type = response.headers.get("Content-Type", "").lower()
    content = response.content.lstrip().lower()
    if (
        not content
        or "html" in content_type
        or content.startswith((b"<html", b"<!doctype html"))
    ):
        raise PriceSourceError(provider, reason="empty" if not content else "schema")

    try:
        return response.json()
    except ValueError as exc:
        raise PriceSourceError(provider, reason="json", cause=exc)
