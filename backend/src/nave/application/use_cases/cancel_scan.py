from dataclasses import dataclass

from nave.application.dto.audit import AuditEntry
from nave.application.dto.views import LogLevel
from nave.application.ports.messaging import ScanQueuePort
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.application.services.authorization import Authorizer
from nave.domain.value_objects.identifiers import ScanId, UserId
from nave.domain.value_objects.role import Role


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelScanCommand:
    actor: UserId
    scan_id: ScanId


@dataclass(frozen=True, slots=True)
class CancelScanResult:
    cancelled: bool
    """False si el escaneo ya había terminado (la operación es idempotente)."""


class CancelScan:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        queue: ScanQueuePort,
        authorizer: Authorizer,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._authorizer = authorizer
        self._clock = clock

    def execute(self, command: CancelScanCommand) -> CancelScanResult:
        with self._uow_factory() as uow:
            scan = uow.scans.get(command.scan_id)
            repository = uow.tenants.get_repository(scan.repository_id)
            self._authorizer.require(
                uow,
                actor=command.actor,
                organization_id=repository.organization_id,
                required=Role.TRIAGER,
                target_type="scan",
                target_id=str(scan.id),
            )
            if scan.is_terminal:
                return CancelScanResult(cancelled=False)
            now = self._clock.now()
            cancelled, event = scan.cancel(now)
            uow.scans.save(cancelled)
            uow.events.publish([event])
            uow.scan_logs.append(scan.id, LogLevel.INFO, "Scan cancelled by a user", now)
            uow.audit.append(
                AuditEntry(
                    occurred_at=now,
                    action="scan.cancelled",
                    actor=command.actor,
                    organization_id=repository.organization_id,
                    target_type="scan",
                    target_id=str(scan.id),
                )
            )
            uow.commit()
        self._queue.revoke(command.scan_id)
        return CancelScanResult(cancelled=True)
