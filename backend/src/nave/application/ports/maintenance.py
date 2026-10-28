from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from nave.application.dto.maintenance import (
    OutboxMessage,
    PurgeCandidate,
    PurgeProgress,
    StaleScan,
)
from nave.domain.value_objects.identifiers import OrganizationId, ScanId


class OutboxStore(Protocol):
    def relay_batch(self, limit: int, deliver: Callable[[Sequence[OutboxMessage]], None]) -> int:
        """Entrega a `deliver` hasta `limit` eventos sin publicar, en orden, y los marca
        publicados en la misma transacción. Si `deliver` lanza, no se marca ninguno
        (entrega al menos una vez). Dos relays a la vez no se pisan. Devuelve cuántos."""
        ...


class EventSinkPort(Protocol):
    def deliver(self, messages: Sequence[OutboxMessage]) -> None:
        """Consume eventos del outbox. Debe ser idempotente (puede repetirse) y no lanzar
        por un evento malformado: un veneno bloquearía el relay entero."""
        ...


class ScanSweepStore(Protocol):
    def queued_before(self, cutoff: datetime, limit: int) -> Sequence[StaleScan]:
        """Escaneos en `QUEUED` con `queued_at < cutoff`, los más antiguos primero."""
        ...

    def running_before(self, cutoff: datetime, limit: int) -> Sequence[StaleScan]:
        """Escaneos en `FETCHING`, `SCANNING` o `PROCESSING` con `started_at < cutoff`."""
        ...

    def mark_requeued(self, scan_id: ScanId, at: datetime) -> None: ...


class RetentionStore(Protocol):
    """Borrados por lotes. Cada método borra como mucho `limit` filas y devuelve cuántas;
    el caso de uso repite hasta agotar. Los plazos los decide la aplicación, no el SQL."""

    def purge_webhook_deliveries(self, before: datetime, limit: int) -> int: ...

    def purge_webhook_counters(self, before: datetime, limit: int) -> int:
        """Contadores por minuto de rechazos y duplicados (ADR 0030)."""
        ...

    def purge_scan_logs(self, before: datetime, limit: int) -> int: ...

    def purge_published_outbox(self, before: datetime, limit: int) -> int: ...

    def purge_scans(self, before: datetime, limit: int) -> int:
        """Escaneos terminados antes de `before` con sus logs, evaluaciones y ocurrencias.
        Conserva los que aún referencia un finding, los que sustituyen a otro y el último
        completado de la rama por defecto de cada repositorio (el baseline)."""
        ...


class OrganizationPurgeStore(Protocol):
    """Borrado de organizaciones desinstaladas (RR-11). La base de datos aplica por sí
    misma el plazo de gracia: este puerto no puede saltárselo."""

    def candidates(self, uninstalled_before: datetime, limit: int) -> Sequence[PurgeCandidate]:
        """Organizaciones `UNINSTALLED` desde antes de `uninstalled_before`, las más
        antiguas primero."""
        ...

    def purge_batch(self, organization_id: OrganizationId) -> PurgeProgress:
        """Borra un lote acotado. Repetir hasta `done`. Lanza `PurgeRefusedError` si la
        organización no es purgable."""
        ...


class OrphanedUserStore(Protocol):
    """Minimización de datos personales. La base de datos impone el plazo y las condiciones."""

    def scrub_batch(self, last_login_before: datetime, limit: int) -> int:
        """Vacía nombre y correo de como mucho `limit` usuarios sin pertenencias, sin sesión
        viva y sin login desde antes de `last_login_before`. Devuelve cuántos."""
        ...
