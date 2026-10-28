import re
from dataclasses import dataclass
from typing import Final

from nave.application.dto.audit import AuditEntry
from nave.application.errors import RepositoryNotScannableError
from nave.application.ports.messaging import ScanQueuePort, ScanStep
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.application.services.authorization import Authorizer
from nave.application.services.scan_scheduler import ScanScheduler
from nave.domain.entities.tenancy import can_be_scanned
from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import RepositoryId, ScanId, UserId
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.trigger import ScanTrigger, TriggerKey

_REQUEST_ID: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.ASCII
)


@dataclass(frozen=True, slots=True, kw_only=True)
class StartScanCommand:
    actor: UserId
    repository_id: RepositoryId
    commit: CommitSha
    ref: GitRef
    request_id: str
    """Clave de idempotencia del cliente (UUID canónico): reintentar la misma petición
    no crea un segundo escaneo."""

    def __post_init__(self) -> None:
        if not fullmatch_str(_REQUEST_ID, self.request_id):
            raise InvalidValueError("request_id", "must be a canonical lowercase UUID")


@dataclass(frozen=True, slots=True)
class StartScanResult:
    scan_id: ScanId
    created: bool


class StartScan:
    """Escaneo manual de un commit. Requiere rol TRIAGER en la organización del repositorio."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        queue: ScanQueuePort,
        scheduler: ScanScheduler,
        authorizer: Authorizer,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._scheduler = scheduler
        self._authorizer = authorizer
        self._clock = clock

    def execute(self, command: StartScanCommand) -> StartScanResult:
        with self._uow_factory() as uow:
            repository = uow.tenants.get_repository(command.repository_id)
            self._authorizer.require(
                uow,
                actor=command.actor,
                organization_id=repository.organization_id,
                required=Role.TRIAGER,
                target_type="repository",
                target_id=str(repository.id),
            )
            organization = uow.tenants.get_organization(repository.organization_id)
            if not can_be_scanned(repository, organization):
                raise RepositoryNotScannableError("repository is not enabled for scanning")
            outcome = self._scheduler.schedule(
                uow,
                repository=repository,
                commit=command.commit,
                ref=command.ref,
                trigger=ScanTrigger.MANUAL,
                trigger_key=TriggerKey(f"manual:{command.request_id}"),
                requested_by=command.actor,
            )
            if outcome.created:
                uow.audit.append(
                    AuditEntry(
                        occurred_at=self._clock.now(),
                        action="scan.requested",
                        actor=command.actor,
                        organization_id=repository.organization_id,
                        target_type="scan",
                        target_id=str(outcome.scan.id),
                        details={"commit": command.commit.value},
                    )
                )
            uow.commit()
        # Fuera de la transacción: si el encolado falla tras el commit, el escaneo queda en
        # QUEUED y el barrido de mantenimiento lo reencola (ID de tarea determinista).
        for superseded in outcome.superseded:
            self._queue.revoke(superseded)
        if outcome.scan.status is ScanStatus.QUEUED:
            self._queue.enqueue(ScanStep.EXECUTE, outcome.scan.id)
        return StartScanResult(scan_id=outcome.scan.id, created=outcome.created)
