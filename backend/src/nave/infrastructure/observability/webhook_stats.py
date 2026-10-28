"""Recuento de rechazos y duplicados de webhooks (puerto `WebhookStatsPort`, ADR 0030).

El rechazo por firma ocurre antes de autenticar: si cada uno costara una escritura, un
atacante sin la clave podría cargar la base de datos. Se acumula en memoria por proceso y
se vuelca como mucho cada `flush_every`; la memoria está acotada por minutos y motivos."""

import logging
import threading
from collections import Counter
from datetime import datetime, timedelta
from typing import Final

from nave.application.dto.platform import WebhookReason
from nave.application.errors import ApplicationError
from nave.application.ports.platform import WebhookCounterStore
from nave.application.ports.system import Clock

_LOG: Final = logging.getLogger("nave.http")
MAX_PENDING_KEYS: Final = 4 * 60


class BufferedWebhookStats:
    def __init__(
        self,
        *,
        store: WebhookCounterStore,
        clock: Clock,
        flush_every: timedelta = timedelta(seconds=30),
    ) -> None:
        self._store = store
        self._clock = clock
        self._flush_every = flush_every
        self._pending: Counter[tuple[datetime, WebhookReason]] = Counter()
        self._last_flush = clock.now()
        self._lock = threading.Lock()

    def count(self, reason: WebhookReason) -> None:
        now = self._clock.now()
        minute = now.replace(second=0, microsecond=0)
        with self._lock:
            key = (minute, reason)
            if key in self._pending or len(self._pending) < MAX_PENDING_KEYS:
                self._pending[key] += 1
            if now - self._last_flush < self._flush_every:
                return
            batch = dict(self._pending)
            self._pending.clear()
            self._last_flush = now
        try:
            self._store.add(batch)
        except ApplicationError as error:
            # Un recuento perdido no debe tumbar la respuesta al webhook.
            _LOG.warning("webhook counters not saved", extra={"error": error.code})
