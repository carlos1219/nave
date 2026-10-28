import logging
from collections.abc import Sequence
from typing import Final

from nave.application.dto.maintenance import OutboxMessage
from nave.application.ports.maintenance import EventSinkPort, OutboxStore

_LOG: Final = logging.getLogger(__name__)


class RelayOutbox:
    """Publica los eventos del outbox a los consumidores (transactional outbox).

    Entrega al menos una vez: si un consumidor falla, el lote entero queda sin marcar y se
    repite en la siguiente ejecución. Por eso los consumidores deben ser idempotentes.
    """

    def __init__(
        self,
        *,
        store: OutboxStore,
        sinks: Sequence[EventSinkPort],
        batch_size: int = 200,
        max_batches: int = 20,
    ) -> None:
        self._store = store
        self._sinks = tuple(sinks)
        self._batch_size = batch_size
        self._max_batches = max_batches

    def execute(self) -> int:
        """Devuelve cuántos eventos se publicaron."""
        total = 0
        for _ in range(self._max_batches):
            published = self._store.relay_batch(self._batch_size, self._deliver)
            total += published
            if published < self._batch_size:
                break
        return total

    def _deliver(self, messages: Sequence[OutboxMessage]) -> None:
        for sink in self._sinks:
            sink.deliver(messages)
        _LOG.debug("outbox batch delivered", extra={"count": len(messages)})
