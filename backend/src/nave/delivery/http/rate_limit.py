"""Limitador de peticiones por cubo de fichas (ADR 0018, AM-815).

En memoria y por proceso: con N procesos el límite efectivo es N veces el configurado. Es una
defensa en profundidad frente a abuso y fuerza bruta, no un contador de facturación; el límite
duro por IP pertenece al proxy inverso. Memoria acotada: nunca más de `MAX_KEYS` cubos.
"""

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from flask import Response, request

from nave.application.ports.system import Clock
from nave.delivery.http.problems import PROBLEMS, problem

MAX_KEYS: Final = 10_000
UNKNOWN_CLIENT: Final = "unknown"
SECONDS_PER_MINUTE: Final = 60


@dataclass(frozen=True, slots=True)
class Bucket:
    burst: int
    per_minute: int

    def __post_init__(self) -> None:
        if self.burst < 1 or self.per_minute < 1:
            raise ValueError("burst and per_minute must be positive")


# Por usuario autenticado.
READ: Final = Bucket(burst=60, per_minute=120)
WRITE: Final = Bucket(burst=10, per_minute=30)
# Por cliente (IP): solo inicio de login y callback. Quien no tiene sesión no puede pagar por
# usuario, y limitar por IP las peticiones con cookie inválida bloquearía a los usuarios
# legítimos que comparten dirección (NAT, o un proxy no declarado en NAVE_TRUSTED_PROXY_HOPS).
LOGIN: Final = Bucket(burst=10, per_minute=20)


class RateLimiter:
    def __init__(self, *, clock: Clock, max_keys: int = MAX_KEYS) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        self._clock = clock
        self._max_keys = max_keys
        self._state: OrderedDict[tuple[str, str], tuple[float, datetime]] = OrderedDict()
        self._lock = threading.Lock()

    def take(self, name: str, key: str, bucket: Bucket) -> int:
        """Consume una ficha. Devuelve 0 si había, o los segundos que faltan."""
        return self._step(name, key, bucket, consume=True)

    def peek(self, name: str, key: str, bucket: Bucket) -> int:
        """Como `take`, sin consumir."""
        return self._step(name, key, bucket, consume=False)

    def _step(self, name: str, key: str, bucket: Bucket, *, consume: bool) -> int:
        now = self._clock.now()
        slot = (name, key)
        with self._lock:
            tokens, updated = self._state.get(slot, (float(bucket.burst), now))
            elapsed = max((now - updated).total_seconds(), 0.0)
            refilled = elapsed * bucket.per_minute / SECONDS_PER_MINUTE
            tokens = min(float(bucket.burst), tokens + refilled)
            if tokens >= 1.0:
                if consume:
                    tokens -= 1.0
                    self._store(slot, tokens, now)
                return 0
            self._store(slot, tokens, now)
            missing = (1.0 - tokens) * SECONDS_PER_MINUTE / bucket.per_minute
            return max(1, math.ceil(missing))

    def _store(self, slot: tuple[str, str], tokens: float, now: datetime) -> None:
        self._state[slot] = (tokens, now)
        self._state.move_to_end(slot)
        while len(self._state) > self._max_keys:
            self._state.popitem(last=False)


def client_key() -> str:
    """IP del cliente. Con `NAVE_TRUSTED_PROXY_HOPS` > 0 Werkzeug ya la ha sustituido por la
    del salto de confianza; sin él es la del par TCP y nunca se lee `X-Forwarded-For` a mano."""
    return request.remote_addr or UNKNOWN_CLIENT


def too_many(retry_after: int) -> Response:
    return problem(PROBLEMS["too_many"], retry_after=retry_after)
