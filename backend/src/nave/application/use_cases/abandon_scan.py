from dataclasses import dataclass
from typing import Final

from nave.application.dto.views import LogLevel
from nave.application.ports.messaging import ScanQueuePort, ScanStep
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.domain.errors import InvalidValueError
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import ReasonCode, StatusReason

_TIMEOUT_CODES: Final = frozenset({ReasonCode.TIMEOUT, ReasonCode.QUEUE_EXPIRED})
_ALLOWED_CODES: Final = _TIMEOUT_CODES | {ReasonCode.INTERNAL_ERROR}


@dataclass(frozen=True, slots=True, kw_only=True)
class AbandonScanCommand:
    """Lo emiten el adaptador de tareas al agotar reintentos y el barrido de
    mantenimiento con escaneos atascados. `detail` puede ser texto no confiable."""

    scan_id: ScanId
    reason_code: ReasonCode
    detail: str = ""

    def __post_init__(self) -> None:
        if self.reason_code not in _ALLOWED_CODES:
            raise InvalidValueError(
                "reason_code", "must be TIMEOUT, QUEUE_EXPIRED or INTERNAL_ERROR"
            )


class AbandonScan:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, queue: ScanQueuePort, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._clock = clock

    def execute(self, command: AbandonScanCommand) -> bool:
        """Devuelve False si el escaneo ya estaba terminado o en PROCESSING con un
        motivo que no le corresponde (no hay nada que abandonar)."""
        with self._uow_factory() as uow:
            scan = uow.scans.get(command.scan_id)
            if scan.is_terminal:
                return False
            now = self._clock.now()
            reason = StatusReason.from_untrusted(command.reason_code, command.detail)
            if command.reason_code in _TIMEOUT_CODES:
                if scan.status is ScanStatus.PROCESSING:
                    return False
                updated, event = scan.time_out(now, reason)
            elif scan.status is ScanStatus.QUEUED:
                # Nunca llegó a ejecutarse: no es un fallo del escaneo sino una expiración en cola.
                updated, event = scan.time_out(now, StatusReason(ReasonCode.QUEUE_EXPIRED))
            else:
                updated, event = scan.fail(now, reason)
            uow.scans.save(updated)
            uow.events.publish([event])
            uow.scan_logs.append(
                scan.id, LogLevel.ERROR, f"Scan abandoned: {command.reason_code.value}", now
            )
            uow.commit()
        self._queue.revoke(command.scan_id)
        self._queue.enqueue(ScanStep.REPORT, command.scan_id)
        return True
