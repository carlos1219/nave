import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from nave.domain.errors import InvalidValueError
from nave.domain.validation import is_int_in_range


def _system_random() -> float:
    return random.SystemRandom().random()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Backoff exponencial con jitter para fallos transitorios (CLAUDE.md §10).

    La espera es `mitad fija + mitad aleatoria` de `base * 2**intento` (tope `cap`): el
    jitter evita que muchas tareas reintenten a la vez tras una caída compartida, y la
    mitad fija garantiza que nunca se reintenta de inmediato. Si el servicio remoto
    exige una espera (`retry_after`), se respeta hasta `retry_after_cap`.
    """

    max_retries: int = 5
    base: timedelta = timedelta(seconds=10)
    cap: timedelta = timedelta(minutes=5)
    retry_after_cap: timedelta = timedelta(minutes=15)
    uniform01: Callable[[], float] = field(default=_system_random, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not is_int_in_range(self.max_retries, 0, 20):
            raise InvalidValueError("max_retries", "must be in [0, 20]")
        if not timedelta(0) < self.base <= self.cap:
            raise InvalidValueError("base", "must be positive and not above cap")
        if self.retry_after_cap < self.cap:
            raise InvalidValueError("retry_after_cap", "must not be below cap")

    @property
    def max_delay(self) -> timedelta:
        return max(self.cap, self.retry_after_cap)

    def delay(self, retries: int, retry_after: timedelta | None = None) -> timedelta:
        """Espera antes del reintento número `retries + 1` (`retries` empieza en 0)."""
        exponent = min(max(retries, 0), 30)
        ceiling = min(self.cap, self.base * (1 << exponent))
        jitter = min(max(self.uniform01(), 0.0), 1.0)
        computed = ceiling / 2 + (ceiling / 2) * jitter
        if retry_after is None:
            return computed
        return min(max(computed, retry_after), self.retry_after_cap)
