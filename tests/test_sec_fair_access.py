"""Behavior tests for SEC fair-access primitives (rate limit + retry)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest

from insider_scanner.core.sec_fair_access import (
    DEFAULT_REQUESTS_PER_SECOND,
    DEFAULT_SEC_RETRY_POLICY,
    RateLimiter,
    SecRetryPolicy,
    backoff_delay,
    default_rate_limiter,
)


class FakeClock:
    """Monotonic clock whose ``sleep`` advances time, recording each wait."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_limiter(interval: float, clock: FakeClock) -> RateLimiter:
    return RateLimiter(
        min_interval_seconds=interval,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def test_first_acquire_does_not_sleep() -> None:
    clock = FakeClock()
    limiter = make_limiter(0.125, clock)

    limiter.acquire()

    assert clock.sleeps == []


def test_successive_acquires_are_spaced_by_min_interval() -> None:
    clock = FakeClock()
    limiter = make_limiter(0.125, clock)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert clock.sleeps == [0.125, 0.125]
    assert clock.now == pytest.approx(0.25)


def test_slow_caller_is_not_throttled() -> None:
    clock = FakeClock()
    limiter = make_limiter(0.125, clock)

    limiter.acquire()
    clock.now += 1.0  # caller did slow work, more than the interval
    limiter.acquire()

    assert clock.sleeps == []


def test_default_rate_limiter_stays_below_sec_ceiling() -> None:
    limiter = default_rate_limiter()

    assert limiter.min_interval_seconds == pytest.approx(1.0 / DEFAULT_REQUESTS_PER_SECOND)
    assert 1.0 / limiter.min_interval_seconds < 10.0


def test_negative_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        RateLimiter(min_interval_seconds=-0.1)


def test_retry_policy_is_frozen() -> None:
    policy = SecRetryPolicy()
    with pytest.raises(FrozenInstanceError):
        policy.max_attempts = 9  # type: ignore[misc]


def test_default_retry_policy_targets_429_and_503() -> None:
    assert DEFAULT_SEC_RETRY_POLICY.retry_statuses == frozenset({429, 503})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SecRetryPolicy(max_attempts=0),
        lambda: SecRetryPolicy(base_delay_seconds=-1.0),
        lambda: SecRetryPolicy(max_delay_seconds=0.5, base_delay_seconds=1.0),
        lambda: SecRetryPolicy(backoff_multiplier=0.5),
    ],
)
def test_invalid_retry_policy_is_rejected(
    factory: Callable[[], SecRetryPolicy],
) -> None:
    with pytest.raises(ValueError):
        factory()


def test_backoff_grows_exponentially_and_is_capped() -> None:
    policy = SecRetryPolicy(
        base_delay_seconds=1.0, max_delay_seconds=5.0, backoff_multiplier=2.0
    )

    delays = [backoff_delay(policy, attempt) for attempt in range(1, 5)]

    assert delays == [1.0, 2.0, 4.0, 5.0]  # last is capped at max_delay


def test_retry_after_overrides_computed_backoff_and_is_capped() -> None:
    policy = SecRetryPolicy(base_delay_seconds=1.0, max_delay_seconds=10.0)

    assert backoff_delay(policy, 1, retry_after=7.0) == 7.0
    assert backoff_delay(policy, 1, retry_after=999.0) == 10.0
    assert backoff_delay(policy, 1, retry_after=-3.0) == 0.0


def test_retry_after_is_ignored_when_policy_disables_it() -> None:
    policy = SecRetryPolicy(respect_retry_after=False, base_delay_seconds=1.0)

    assert backoff_delay(policy, 1, retry_after=7.0) == 1.0


def test_injected_jitter_is_applied_within_cap() -> None:
    policy = SecRetryPolicy(base_delay_seconds=2.0, max_delay_seconds=10.0)

    assert backoff_delay(policy, 1, jitter=lambda d: d + 0.5) == 2.5
