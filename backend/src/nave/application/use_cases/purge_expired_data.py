import logging
from collections.abc import Callable
from datetime import datetime
from typing import Final

from nave.application.dto.maintenance import RetentionPolicy, RetentionReport
from nave.application.ports.maintenance import RetentionStore
from nave.application.ports.system import Clock

_LOG: Final = logging.getLogger(__name__)


class PurgeExpiredData:
    """Aplica los plazos de retención de ADR 0006. Nunca toca `audit_log` ni
    `triage_decisions`: el puerto no ofrece cómo."""

    def __init__(self, *, store: RetentionStore, clock: Clock, policy: RetentionPolicy) -> None:
        self._store = store
        self._clock = clock
        self._policy = policy

    def execute(self) -> RetentionReport:
        now = self._clock.now()
        p = self._policy
        return RetentionReport(
            webhook_deliveries=self._drain(
                self._store.purge_webhook_deliveries, now - p.webhook_deliveries
            ),
            scan_logs=self._drain(self._store.purge_scan_logs, now - p.scan_logs),
            published_outbox=self._drain(
                self._store.purge_published_outbox, now - p.published_outbox
            ),
            scans=self._drain(self._store.purge_scans, now - p.scans),
            # Mismo plazo que las entregas: son la otra mitad de la misma vista (ADR 0030).
            webhook_counters=self._drain(
                self._store.purge_webhook_counters, now - p.webhook_deliveries
            ),
        )

    def _drain(self, purge: Callable[[datetime, int], int], before: datetime) -> int:
        total = 0
        for _ in range(self._policy.max_batches):
            deleted = purge(before, self._policy.batch_size)
            total += deleted
            if deleted < self._policy.batch_size:
                break
        return total
