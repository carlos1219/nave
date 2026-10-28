from dataclasses import dataclass

from nave.application.dto.views import LogLevel
from nave.application.errors import IdempotencyConflictError
from nave.application.ports.persistence import UnitOfWork
from nave.application.ports.system import Clock, IdGenerator
from nave.domain.entities.scan import Scan
from nave.domain.entities.tenancy import Repository
from nave.domain.events import DomainEvent
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import ScanId, UserId
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.trigger import ScanTrigger, TriggerKey


@dataclass(frozen=True, slots=True)
class ScheduleOutcome:
    scan: Scan
    created: bool
    superseded: tuple[ScanId, ...]


class ScanScheduler:
    """Alta de escaneos compartida por la petición manual y el webhook.

    - Idempotente por `TriggerKey`: la misma clave devuelve el mismo escaneo.
    - Un escaneo nuevo de una rama sustituye a los pendientes de esa rama (AM-203),
      salvo a los que ya están en PROCESSING, que no se pueden cancelar.
    """

    def __init__(self, *, clock: Clock, ids: IdGenerator) -> None:
        self._clock = clock
        self._ids = ids

    def schedule(  # noqa: PLR0913 - datos del escaneo a crear
        self,
        uow: UnitOfWork,
        *,
        repository: Repository,
        commit: CommitSha,
        ref: GitRef,
        trigger: ScanTrigger,
        trigger_key: TriggerKey,
        pull_request_number: int | None = None,
        requested_by: UserId | None = None,
    ) -> ScheduleOutcome:
        existing = uow.scans.find_by_trigger_key(trigger_key)
        if existing is not None:
            if existing.repository_id != repository.id or existing.commit != commit:
                raise IdempotencyConflictError("trigger key already used for another scan")
            return ScheduleOutcome(scan=existing, created=False, superseded=())
        now = self._clock.now()
        scan, queued = Scan.queue(
            scan_id=ScanId(self._ids.new_uuid()),
            repository_id=repository.id,
            commit=commit,
            ref=ref,
            trigger=trigger,
            trigger_key=trigger_key,
            at=now,
            pull_request_number=pull_request_number,
            requested_by=requested_by,
        )
        events: list[DomainEvent] = [queued]
        superseded: list[ScanId] = []
        # Se leen antes de insertar el nuevo (no debe contarse a sí mismo) y se cancelan
        # después: `scans.superseded_by` es una clave foránea inmediata hacia `scans.id`, así
        # que el escaneo nuevo tiene que existir cuando se enlaza desde el antiguo.
        active_scans = uow.scans.find_active_for_ref(repository.id, ref)
        uow.scans.add(scan)
        uow.scan_logs.append(scan.id, LogLevel.INFO, "Scan queued", now)
        for active in active_scans:
            if active.status is ScanStatus.PROCESSING:
                continue
            cancelled, event = active.supersede(scan.id, now)
            uow.scans.save(cancelled)
            uow.scan_logs.append(active.id, LogLevel.INFO, f"Superseded by scan {scan.id}", now)
            events.append(event)
            superseded.append(active.id)
        uow.events.publish(events)
        return ScheduleOutcome(scan=scan, created=True, superseded=tuple(superseded))
