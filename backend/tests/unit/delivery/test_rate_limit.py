from datetime import timedelta

import pytest

from nave.delivery.http.rate_limit import Bucket, RateLimiter
from tests.fakes.memory import FakeClock

BUCKET = Bucket(burst=3, per_minute=60)  # 1 ficha por segundo


def make() -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    return RateLimiter(clock=clock), clock


def test_allows_the_burst_then_rejects_with_wait() -> None:
    limiter, _ = make()
    assert [limiter.take("n", "k", BUCKET) for _ in range(3)] == [0, 0, 0]
    assert limiter.take("n", "k", BUCKET) == 1


def test_refills_over_time_but_never_beyond_the_burst() -> None:
    limiter, clock = make()
    for _ in range(3):
        limiter.take("n", "k", BUCKET)
    clock.advance(seconds=2)
    assert [limiter.take("n", "k", BUCKET) for _ in range(3)] == [0, 0, 1]
    clock.advance(hours=5)
    assert [limiter.take("n", "k", BUCKET) for _ in range(4)] == [0, 0, 0, 1]


def test_retry_after_is_rounded_up_and_at_least_one_second() -> None:
    limiter, clock = make()
    slow = Bucket(burst=1, per_minute=1)
    limiter.take("n", "k", slow)
    assert limiter.take("n", "k", slow) == 60
    clock.advance(seconds=59.5)
    assert limiter.take("n", "k", slow) == 1


def test_keys_and_names_are_independent() -> None:
    limiter, _ = make()
    for _ in range(3):
        limiter.take("a", "k", BUCKET)
    assert limiter.take("a", "k", BUCKET) == 1
    assert limiter.take("a", "other", BUCKET) == 0
    assert limiter.take("b", "k", BUCKET) == 0


def test_peek_does_not_consume() -> None:
    limiter, _ = make()
    for _ in range(10):
        assert limiter.peek("n", "k", BUCKET) == 0
    assert [limiter.take("n", "k", BUCKET) for _ in range(3)] == [0, 0, 0]
    assert limiter.peek("n", "k", BUCKET) == 1


def test_clock_going_backwards_does_not_grant_tokens() -> None:
    limiter, clock = make()
    for _ in range(3):
        limiter.take("n", "k", BUCKET)
    clock.current -= timedelta(hours=1)
    assert limiter.take("n", "k", BUCKET) == 1


def test_memory_is_bounded_and_evicts_the_least_recently_used() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, max_keys=2)
    one = Bucket(burst=1, per_minute=1)
    limiter.take("n", "a", one)
    limiter.take("n", "b", one)
    limiter.take("n", "c", one)  # expulsa "a"
    assert limiter.take("n", "b", one) > 0
    assert limiter.take("n", "a", one) == 0  # su estado se perdió: vuelve a empezar


@pytest.mark.parametrize(("burst", "rate"), [(0, 1), (1, 0), (-1, 5)])
def test_rejects_degenerate_buckets(burst: int, rate: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        Bucket(burst=burst, per_minute=rate)


def test_rejects_zero_capacity() -> None:
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(clock=FakeClock(), max_keys=0)
