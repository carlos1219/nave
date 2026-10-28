from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final
from urllib.parse import urlsplit

from nave.domain.errors import InvalidValueError
from nave.domain.validation import is_int_in_range
from nave.infrastructure.queue.retry import RetryPolicy

_MIN_KILL_GRACE: Final = timedelta(seconds=30)
_VISIBILITY_MARGIN: Final = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class TaskTimeLimits:
    """`soft` lanza `SoftTimeLimitExceeded` dentro de la tarea (permite limpiar y
    abandonar con motivo); `hard` mata el proceso hijo. Entre ambos debe caber la
    limpieza (cierre del workspace, transacción de abandono)."""

    soft: timedelta
    hard: timedelta

    def __post_init__(self) -> None:
        if self.soft <= timedelta(0):
            raise InvalidValueError("soft", "must be positive")
        if self.hard - self.soft < _MIN_KILL_GRACE:
            raise InvalidValueError("hard", "must leave at least 30s after the soft limit")


@dataclass(frozen=True, slots=True)
class CelerySettings:
    broker_url: str = field(repr=False)
    scan: TaskTimeLimits
    report: TaskTimeLimits = field(
        default_factory=lambda: TaskTimeLimits(timedelta(minutes=2), timedelta(minutes=3))
    )
    maintenance: TaskTimeLimits = field(
        default_factory=lambda: TaskTimeLimits(timedelta(minutes=4), timedelta(minutes=5))
    )
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    max_queue_depth: int = 1000
    max_tasks_per_child: int = 25

    def __post_init__(self) -> None:
        parts = urlsplit(self.broker_url)
        if parts.scheme not in {"redis", "rediss"} or not parts.hostname:
            raise InvalidValueError("broker_url", "must be a redis:// or rediss:// URL")
        if not is_int_in_range(self.max_queue_depth, 1, 1_000_000):
            raise InvalidValueError("max_queue_depth", "must be in [1, 1000000]")
        if not is_int_in_range(self.max_tasks_per_child, 1, 10_000):
            raise InvalidValueError("max_tasks_per_child", "must be in [1, 10000]")

    @property
    def visibility_timeout(self) -> timedelta:
        """Con Redis, un mensaje no confirmado se reentrega al vencer este plazo. Debe
        superar lo que una tarea puede tardar (límite duro + el mayor retardo de
        reintento, que se mantiene sin confirmar en el worker), o la misma tarea
        correría dos veces a la vez."""
        longest = max(self.scan.hard, self.report.hard, self.maintenance.hard)
        return longest + self.retry.max_delay + _VISIBILITY_MARGIN
