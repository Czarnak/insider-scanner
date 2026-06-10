"""Price provider registry and selection configuration."""

from __future__ import annotations

import os

from .source import PriceSource
from .tiingo import TiingoSource
from .yahoo import YahooSource

def get_price_source(name: str | None = None, api_key: str | None = None) -> PriceSource:
    """Resolve and configure a price provider based on arguments or environment."""
    provider_name = name
    if provider_name is None:
        provider_name = os.environ.get("PRICE_SOURCE")

    if provider_name is not None:
        provider_name = provider_name.strip().lower()
        if not provider_name:
            provider_name = None

    key = api_key
    if key is None:
        key = os.environ.get("TIINGO_API_KEY")
    
    if key is not None:
        key = key.strip()
        if not key:
            key = None

    if provider_name == "yahoo":
        return YahooSource()
    elif provider_name == "tiingo":
        if not key:
            raise ValueError("TIINGO_API_KEY is required when using the tiingo provider.")
        return TiingoSource(key)
    elif provider_name is not None:
        raise ValueError(f"Unknown price provider: {provider_name}")

    # No provider specified
    if key:
        return TiingoSource(key)
    
    return YahooSource()
