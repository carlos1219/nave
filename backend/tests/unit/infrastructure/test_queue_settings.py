from datetime import timedelta

import pytest

from nave.domain.errors import InvalidValueError
from nave.infrastructure.queue.retry import RetryPolicy
from nave.infrastructure.queue.settings import CelerySettings, TaskTimeLimits

BROKER = "redis://default:s3cr3t-pw@redis:6379/0"


def limits(soft: int = 600, hard: int = 660) -> TaskTimeLimits:
    return TaskTimeLimits(timedelta(seconds=soft), timedelta(seconds=hard))


def test_repr_never_shows_the_broker_password() -> None:
    assert "s3cr3t-pw" not in repr(CelerySettings(broker_url=BROKER, scan=limits()))


def test_visibility_timeout_outlives_the_longest_task_plus_the_longest_retry_wait() -> None:
    settings = CelerySettings(broker_url=BROKER, scan=limits(1500, 1560), retry=RetryPolicy())
    assert settings.visibility_timeout > timedelta(seconds=1560) + settings.retry.max_delay


@pytest.mark.parametrize(("soft", "hard"), [(0, 100), (100, 110), (100, 100)])
def test_time_limits_need_room_between_soft_and_hard(soft: int, hard: int) -> None:
    with pytest.raises(InvalidValueError):
        limits(soft, hard)


@pytest.mark.parametrize(
    "url", ["", "http://redis/0", "redis://", "amqp://guest@host//", "memory://", "redis:///0"]
)
def test_only_redis_brokers_are_accepted(url: str) -> None:
    with pytest.raises(InvalidValueError):
        CelerySettings(broker_url=url, scan=limits())


@pytest.mark.parametrize(("field", "value"), [("max_queue_depth", 0), ("max_tasks_per_child", 0)])
def test_numeric_bounds(field: str, value: int) -> None:
    with pytest.raises(InvalidValueError):
        CelerySettings(broker_url=BROKER, scan=limits(), **{field: value})  # type: ignore[arg-type]
