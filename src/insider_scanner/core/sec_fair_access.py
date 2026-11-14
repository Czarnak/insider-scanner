"""Operational fair-access primitives for SEC ingestion (rate limit + retry).

These are deliberately separate from :mod:`insider_scanner.core.sec_security`
(which owns *trust* boundaries).  This module owns *fairness and resilience*:
keeping request rate under SEC's published ceiling and retrying transient
server-side throttling responses with bounded backoff.

The shared limiter and circuit breaker coordinate SEC access across clients.
Clocks and sleep functions are injectable so tests never wall-clock sleep.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock

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


_SHARED_RATE_LIMITER = RateLimiter(
    min_interval_seconds=1.0 / DEFAULT_REQUESTS_PER_SECOND
)


def default_rate_limiter() -> RateLimiter:
    """Return the process-wide SEC gate shared by all default clients."""
    return _SHARED_RATE_LIMITER


@dataclass(frozen=True, slots=True)
class SecRetryPolicy:
    """Immutable bounded-retry policy for transient SEC throttling responses."""

    max_attempts: int = 4
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    retry_statuses: frozenset[int] = frozenset({403, 429, 500, 502, 503, 504})
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


class SecCircuitOpenError(Exception):
    """Internal gate signal converted to a public SEC client error."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("SEC fair-access circuit is open")


@dataclass(slots=True)
class SecCircuitBreaker:
    """Stop sustained SEC blocking without sleeping through a long cooldown.

    A generation ticket prevents older in-flight successes from closing a newly
    opened circuit. After cooldown only one request may probe SEC access.
    """

    failure_threshold: int = 2
    cooldown_seconds: float = 600.0
    monotonic: Callable[[], float] = time.monotonic
    _lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)
    _failures: int = field(default=0, init=False, repr=False)
    _blocked_until: float | None = field(default=None, init=False, repr=False)
    _probe_in_flight: bool = field(default=False, init=False, repr=False)
    _generation: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if not math.isfinite(self.cooldown_seconds) or self.cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be finite and positive")

    def acquire(self) -> int:
        """Return an attempt ticket or reject traffic during cooldown/probe."""
        with self._lock:
            if self._blocked_until is not None:
                remaining = self._blocked_until - self.monotonic()
                if remaining > 0 or self._probe_in_flight:
                    raise SecCircuitOpenError(max(remaining, 1.0))
                self._probe_in_flight = True
            return self._generation

    def record_response(
        self, ticket: int, status: int, retry_after: float | None = None
    ) -> float | None:
        """Record an HTTP response, returning cooldown when it opens the gate."""
        with self._lock:
            if ticket != self._generation:
                return None
            if status in {403, 429}:
                self._failures += 1
                if (
                    self._probe_in_flight
                    or self._failures >= self.failure_threshold
                    or (retry_after is not None and retry_after > 30.0)
                ):
                    return self._open(retry_after)
            elif self._probe_in_flight and status in {500, 502, 503, 504}:
                return self._open(retry_after)
            else:
                self._failures = 0
                self._blocked_until = None
            self._probe_in_flight = False
            return None

    def record_transport_failure(self, ticket: int) -> None:
        """A failed half-open probe reopens the circuit instead of wedging it."""
        with self._lock:
            if ticket == self._generation and self._probe_in_flight:
                self._open(None)

    def _open(self, retry_after: float | None) -> float:
        delay = max(self.cooldown_seconds, retry_after or 0.0)
        self._blocked_until = self.monotonic() + delay
        self._probe_in_flight = False
        self._generation += 1
        return delay


_SHARED_CIRCUIT_BREAKER = SecCircuitBreaker()


def default_circuit_breaker() -> SecCircuitBreaker:
    """Return the SEC block state shared by default clients in this process."""
    return _SHARED_CIRCUIT_BREAKER


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
