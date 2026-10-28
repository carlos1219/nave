from collections.abc import Sequence
from enum import Enum
from typing import Protocol

from nave.domain.events import DomainEvent
from nave.domain.value_objects.identifiers import ScanId


class ScanStep(Enum):
    """Trabajos encolables. EXECUTE (cola `scan`) obtiene, escanea y procesa en una
    sola tarea para que el workspace no sobreviva a ella (ADR 0009)."""

    EXECUTE = "EXECUTE"
    REPORT = "REPORT"


class ScanQueuePort(Protocol):
    def enqueue(self, step: ScanStep, scan_id: ScanId) -> None:
        """Encola un paso con ID de tarea determinista (`{scan_id}:{step}`).

        Encolar dos veces es seguro porque los casos de uso son idempotentes, no porque
        el broker deduplique. Lanza `QueueUnavailableError` si el broker no lo acepta.
        """
        ...

    def revoke(self, scan_id: ScanId) -> None:
        """Retira de la cola los pasos pendientes de un escaneo. Idempotente y de mejor
        esfuerzo: nunca lanza por fallos del broker. La cancelación efectiva la decide
        PostgreSQL; una tarea ya iniciada se detiene al releer un estado terminal."""
        ...


class EventPublisherPort(Protocol):
    def publish(self, events: Sequence[DomainEvent]) -> None:
        """Escribe los eventos en el outbox de la transacción en curso.

        Se publican tras el commit; si la transacción se deshace, no se publican.
        """
        ...
