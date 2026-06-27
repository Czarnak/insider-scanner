"""Operational fair-access primitives for SEC ingestion (rate limit + retry).

These are deliberately separate from :mod:`insider_scanner.core.sec_security`
(which owns *trust* boundaries).  This module owns *fairness and resilience*:
keeping request rate under SEC's published ceiling and retrying transient
server-side throttling responses with bounded backoff.

The rate limiter holds the only mutable state in the SEC client chain; both the
monotonic clock and the sleep function are injectable so tests are deterministic
and never wall-clock sleep.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
import time

# SEC fair-access guidance caps automated traffic at 10 requests/second. We
# default below that ceiling to absorb jitter and stay comfortably compliant.
SEC_MAX_REQUESTS_PER_SECOND = 10.0
DEFAULT_REQUESTS_PER_SECOND = 8.0


def no_jitter(delay: float) -> float:
    """Identity jitter — deterministic by default; callers may inject spread."""
    return delay


@dataclass(slots=True)
class RateLimiter:
    """Minimum-interval gate that bounds request rate across threads.

    ``acquire`` blocks (via the injected ``sleep``) until at least
    ``min_interval_seconds`` has elapsed since the previous acquisition. The
    first acquisition never sleeps. The clock is re-read after sleeping so the
    limiter behaves correctly with both real and fake clocks.
    """

    min_interval_seconds: float
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)
    _next_allowed: float | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")

    def acquire(self) -> None:
        with self._lock:
            if self._next_allowed is not None:
                wait = self._next_allowed - self.monotonic()
                if wait > 0:
                    self.sleep(wait)
            self._next_allowed = self.monotonic() + self.min_interval_seconds


def default_rate_limiter() -> RateLimiter:
    """Return a limiter pinned to :data:`DEFAULT_REQUESTS_PER_SECOND`."""
    return RateLimiter(min_interval_seconds=1.0 / DEFAULT_REQUESTS_PER_SECOND)


@dataclass(frozen=True, slots=True)
class SecRetryPolicy:
    """Immutable bounded-retry policy for transient SEC throttling responses."""

    max_attempts: int = 4
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    retry_statuses: frozenset[int] = frozenset({429, 503})
    respect_retry_after: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must not be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be at least 1")


DEFAULT_SEC_RETRY_POLICY = SecRetryPolicy()


def backoff_delay(
    policy: SecRetryPolicy,
    attempt: int,
    *,
    retry_after: float | None = None,
    jitter: Callable[[float], float] = no_jitter,
) -> float:
    """Return the wait (seconds) before the retry following a failed *attempt*.

    *attempt* is the 1-based number of the attempt that just failed. A present,
    honored ``Retry-After`` overrides computed backoff. The result is always
    bounded by ``policy.max_delay_seconds``.
    """
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    if policy.respect_retry_after and retry_after is not None:
        return min(max(retry_after, 0.0), policy.max_delay_seconds)
    raw = policy.base_delay_seconds * (policy.backoff_multiplier ** (attempt - 1))
    return min(max(jitter(raw), 0.0), policy.max_delay_seconds)
