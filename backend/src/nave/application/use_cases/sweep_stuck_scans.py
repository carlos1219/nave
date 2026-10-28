import logging
from datetime import datetime
from typing import Final

from nave.application.dto.maintenance import StaleScan, SweepReport, SweepThresholds
from nave.application.errors import (
    ConcurrentModificationError,
    NotFoundError,
    QueueUnavailableError,
)
from nave.application.ports.maintenance import ScanSweepStore
from nave.application.ports.messaging import ScanQueuePort, ScanStep
from nave.application.ports.system import Clock
from nave.application.use_cases.abandon_scan import AbandonScan, AbandonScanCommand
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import ReasonCode

_LOG: Final = logging.getLogger(__name__)


class SweepStuckScans:
    """Recupera escaneos que ningún worker va a terminar (AM-308).

    1. En cola demasiado tiempo (el proceso cayó entre el commit y el encolado, o el
       mensaje se perdió): se reencola una sola vez; si sigue en cola al llegar al plazo
       de expiración, expira con `QUEUE_EXPIRED`.
    2. En curso más tiempo del que cualquier tarea sana puede tardar (worker muerto, mensaje
       venenoso que mata al worker en bucle): se abandona. El mensaje reentregado verá un
       estado terminal y se descartará.

    Idempotente y segura ante ejecuciones solapadas: cada acción se revalida en su propia
    transacción y un conflicto solo se cuenta como omitido.
    """

    def __init__(
        self,
        *,
        store: ScanSweepStore,
        queue: ScanQueuePort,
        abandon: AbandonScan,
        clock: Clock,
        thresholds: SweepThresholds,
    ) -> None:
        self._store = store
        self._queue = queue
        self._abandon = abandon
        self._clock = clock
        self._t = thresholds

    def execute(self) -> SweepReport:
        now = self._clock.now()
        counts = {"requeued": 0, "expired": 0, "abandoned": 0, "skipped": 0}
        queued = self._store.queued_before(now - self._t.requeue_after, self._t.batch_size)
        for scan in queued:
            self._handle_queued(scan, now, counts)
        running = self._store.running_before(now - self._t.running_stuck_after, self._t.batch_size)
        for scan in running:
            self._handle_running(scan, counts)
        return SweepReport(
            requeued=counts["requeued"],
            expired_in_queue=counts["expired"],
            abandoned_running=counts["abandoned"],
            skipped=counts["skipped"],
        )

    def _handle_queued(self, scan: StaleScan, now: datetime, counts: dict[str, int]) -> None:
        if now - scan.since >= self._t.expire_queued_after:
            outcome = self._abandon_scan(scan, ReasonCode.QUEUE_EXPIRED)
            counts["expired" if outcome else "skipped"] += 1
            return
        if scan.requeued_at is not None:
            return
        try:
            self._queue.enqueue(ScanStep.EXECUTE, scan.scan_id)
            self._store.mark_requeued(scan.scan_id, now)
        except QueueUnavailableError:
            # Cola llena o broker caído: se vuelve a intentar en el siguiente barrido.
            _LOG.warning("requeue postponed", extra={"scan_id": str(scan.scan_id)})
            counts["skipped"] += 1
            return
        counts["requeued"] += 1

    def _handle_running(self, scan: StaleScan, counts: dict[str, int]) -> None:
        code = (
            ReasonCode.INTERNAL_ERROR
            if scan.status is ScanStatus.PROCESSING
            else ReasonCode.TIMEOUT
        )
        outcome = self._abandon_scan(scan, code)
        counts["abandoned" if outcome else "skipped"] += 1

    def _abandon_scan(self, scan: StaleScan, code: ReasonCode) -> bool:
        try:
            return self._abandon.execute(
                AbandonScanCommand(scan_id=scan.scan_id, reason_code=code, detail="stuck scan")
            )
        except (ConcurrentModificationError, NotFoundError):
            return False
