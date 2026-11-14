"""Price provider contracts and shared request validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date as Date
from typing import Protocol, runtime_checkable

import requests

from .model import PriceBar, normalize_symbol

_PROVIDER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}", re.ASCII)


@runtime_checkable
class PriceSource(Protocol):
    """Provider capable of fetching validated daily price bars."""

    def fetch_daily(self, symbol: str, start: Date, end: Date) -> list[PriceBar]:
        """Fetch daily bars for an inclusive date range."""
        ...


class PriceSourceError(RuntimeError):
    """Sanitized price provider failure."""

    def __init__(
        self,
        provider: str,
        *,
        context: Mapping[str, object] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        del context, cause
        self.provider = _sanitized_provider(provider)
        super().__init__(f"{self.provider} price request failed")


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
        raise PriceSourceError(provider)

    content_type = response.headers.get("Content-Type", "").lower()
    content = response.content.lstrip().lower()
    if (
        not content
        or "html" in content_type
        or content.startswith((b"<html", b"<!doctype html"))
    ):
        raise PriceSourceError(provider)

    try:
        return response.json()
    except ValueError:
        raise PriceSourceError(provider) from None
