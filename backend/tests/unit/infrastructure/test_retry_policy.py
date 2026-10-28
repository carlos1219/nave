from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nave.domain.errors import InvalidValueError
from nave.infrastructure.queue.retry import RetryPolicy


def policy(u: float = 0.5, **kwargs: object) -> RetryPolicy:
    return RetryPolicy(uniform01=lambda: u, **kwargs)  # type: ignore[arg-type]


def test_delay_doubles_until_the_cap() -> None:
    p = policy(u=1.0, base=timedelta(seconds=10), cap=timedelta(seconds=60))
    assert [p.delay(n).total_seconds() for n in range(5)] == [10, 20, 40, 60, 60]


def test_jitter_keeps_half_of_the_delay_fixed() -> None:
    low = policy(u=0.0, base=timedelta(seconds=10)).delay(0)
    high = policy(u=1.0, base=timedelta(seconds=10)).delay(0)
    assert (low, high) == (timedelta(seconds=5), timedelta(seconds=10))


@given(retries=st.integers(min_value=-5, max_value=10_000), u=st.floats(-10, 10, allow_nan=False))
def test_delay_is_always_positive_and_bounded(retries: int, u: float) -> None:
    p = policy(u=u)
    delay = p.delay(retries)
    assert timedelta(0) < delay <= p.cap


def test_a_misbehaving_random_source_cannot_break_the_bounds() -> None:
    p = RetryPolicy(uniform01=lambda: float("inf"))
    assert p.delay(0) <= p.cap


def test_retry_after_is_honoured_and_capped() -> None:
    p = policy(u=0.0, base=timedelta(seconds=10), cap=timedelta(seconds=60))
    assert p.delay(0, retry_after=timedelta(seconds=120)) == timedelta(seconds=120)
    assert p.delay(0, retry_after=timedelta(hours=5)) == p.retry_after_cap
    assert p.delay(3, retry_after=timedelta(seconds=1)) >= timedelta(seconds=30)


def test_max_delay_is_the_largest_possible_wait() -> None:
    assert policy().max_delay == timedelta(minutes=15)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1},
        {"max_retries": 21},
        {"base": timedelta(0)},
        {"base": timedelta(minutes=10)},
        {"retry_after_cap": timedelta(seconds=1)},
    ],
)
def test_invalid_policies_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(InvalidValueError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]
