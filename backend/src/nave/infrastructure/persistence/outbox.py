from collections.abc import Sequence

from sqlalchemy import Connection, insert

from nave.domain.events import DomainEvent
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.codec import encode_event


class SqlOutbox:
    """`EventPublisherPort` con transactional outbox: los eventos se escriben en la misma
    transacción que el cambio de estado. Si esta se deshace, no existieron. El relay que
    los publica es otro componente (fase 3, Celery)."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def publish(self, events: Sequence[DomainEvent]) -> None:
        if not events:
            return
        rows = []
        for event in events:
            event_type, payload = encode_event(event)
            rows.append({"type": event_type, "payload": payload, "occurred_at": event.occurred_at})
        self._conn.execute(insert(t.outbox_events), rows)
