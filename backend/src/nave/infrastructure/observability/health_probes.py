"""Comprobaciones de salud (puerto `HealthProbesPort`, ADR 0030).

Cada sonda tiene un tiempo máximo corto y nunca lanza: un fallo es un DOWN con un detalle
genérico. Los detalles no incluyen URLs, credenciales ni texto de excepciones."""

import http.client
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from celery import Celery
from sqlalchemy import Engine, text

from nave.application.dto.platform import ComponentHealth, HealthComponent, HealthStatus
from nave.application.ports.system import Clock
from nave.infrastructure.queue.names import MAINTENANCE_QUEUE, REPORT_QUEUE, SCAN_QUEUE

TIMEOUT_SECONDS: Final = 3.0
SLOW_MS: Final = 1500
_QUEUES: Final = (
    (HealthComponent.QUEUE_SCAN, SCAN_QUEUE),
    (HealthComponent.QUEUE_REPORT, REPORT_QUEUE),
    (HealthComponent.QUEUE_MAINTENANCE, MAINTENANCE_QUEUE),
)


@dataclass(frozen=True, slots=True)
class ProbeTargets:
    runner_url: str | None = None
    """Solo el worker: la API no tiene red hacia `scan-runner`."""
    egress_proxy_url: str | None = None
    max_queue_depth: int = 1000


class HealthProbes:
    def __init__(
        self, *, engine: Engine, celery: Celery, clock: Clock, targets: ProbeTargets
    ) -> None:
        self._engine = engine
        self._celery = celery
        self._clock = clock
        self._targets = targets

    def probe(self) -> Sequence[ComponentHealth]:
        found = [self._timed(HealthComponent.DATABASE, self._database)]
        found += self._broker_and_queues()
        if self._targets.runner_url is not None:
            url = self._targets.runner_url
            found.append(self._timed(HealthComponent.SCAN_RUNNER, lambda: _http_ok(url)))
        if self._targets.egress_proxy_url is not None:
            proxy = self._targets.egress_proxy_url
            found.append(self._timed(HealthComponent.EGRESS_PROXY, lambda: _tcp_ok(proxy)))
        return found

    def _timed(self, component: HealthComponent, check: Callable[[], str]) -> ComponentHealth:
        start = time.monotonic()
        try:
            detail = check()
        except Exception:  # noqa: BLE001 - cualquier fallo de la sonda es un DOWN, sin detalle
            return ComponentHealth.of(
                component, HealthStatus.DOWN, "No responde", self._clock.now()
            )
        elapsed = int((time.monotonic() - start) * 1000)
        status = HealthStatus.DEGRADED if elapsed > SLOW_MS else HealthStatus.OK
        return ComponentHealth.of(component, status, detail, self._clock.now(), elapsed)

    def _database(self) -> str:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return "Responde"

    def _broker_and_queues(self) -> list[ComponentHealth]:
        start = time.monotonic()
        depths: dict[str, int] = {}
        try:
            with self._celery.connection_for_write() as connection:
                client: Any = connection.default_channel.client
                client.ping()
                for _, queue in _QUEUES:
                    depth = client.llen(queue)
                    depths[queue] = depth if isinstance(depth, int) else -1
        except Exception:  # noqa: BLE001 - broker caído: todas sus filas son DOWN
            now = self._clock.now()
            return [
                ComponentHealth.of(c, HealthStatus.DOWN, "Broker no disponible", now)
                for c in (HealthComponent.BROKER, *(c for c, _ in _QUEUES))
            ]
        elapsed = int((time.monotonic() - start) * 1000)
        now = self._clock.now()
        found = [
            ComponentHealth.of(
                HealthComponent.BROKER,
                HealthStatus.DEGRADED if elapsed > SLOW_MS else HealthStatus.OK,
                "Responde",
                now,
                elapsed,
            )
        ]
        limit = self._targets.max_queue_depth
        for component, queue in _QUEUES:
            depth = depths[queue]
            if depth < 0:
                status, detail = HealthStatus.DEGRADED, "Profundidad desconocida"
            else:
                full = depth >= limit
                busy = depth >= limit // 2
                status = (
                    HealthStatus.DOWN
                    if full
                    else HealthStatus.DEGRADED
                    if busy
                    else HealthStatus.OK
                )
                detail = f"{depth} en espera (máximo {limit})"
            found.append(ComponentHealth.of(component, status, detail, now))
        return found


def _http_ok(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname is None:
        raise ValueError("unsupported runner url")
    connection = http.client.HTTPConnection(
        parts.hostname, parts.port or 80, timeout=TIMEOUT_SECONDS
    )
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        response.read(1024)
    finally:
        connection.close()
    if response.status != http.client.OK:
        raise ConnectionError("unhealthy")
    return "Responde"


def _tcp_ok(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname is None or parts.port is None:
        raise ValueError("unsupported proxy url")
    socket.create_connection((parts.hostname, parts.port), timeout=TIMEOUT_SECONDS).close()
    return "Acepta conexiones"
