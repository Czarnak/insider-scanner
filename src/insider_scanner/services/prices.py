"""Application-owned price provider and persistence lifecycle."""

from __future__ import annotations

from datetime import date
from threading import Lock

from sqlalchemy.engine import Engine

from insider_scanner.core.prices import registry, service
from insider_scanner.core.prices.model import PriceBar
from insider_scanner.core.prices.source import PriceSource


class PriceService:
    """Reuse one provider across chart loads using the application's engine.

    Provider creation stays lazy so unrelated commands do not require price
    credentials. The lock keeps the provider's HTTP session and cache refreshes
    within one operation even when GUI requests move between worker threads.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._source: PriceSource | None = None
        self._lock = Lock()
        self._closed = False

    def get_history(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        """Load cached daily prices and refresh missing provider coverage."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Price service is closed")
            if self._source is None:
                self._source = registry.get_price_source()
            return service.get_price_history(
                symbol, start, end, source=self._source, engine=self._engine
            )

    def close(self) -> None:
        """Release the provider after the application's workers have stopped."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._source is not None:
                close = getattr(self._source, "close", None)
                if close is not None:
                    close()
