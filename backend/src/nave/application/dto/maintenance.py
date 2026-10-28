from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from nave.domain.errors import InvalidValueError
from nave.domain.validation import is_int_in_range
from nave.domain.value_objects.identifiers import OrganizationId, ScanId
from nave.domain.value_objects.scan_status import ScanStatus

_MIN_RETENTION: timedelta = timedelta(days=1)
_MIN_SCAN_RETENTION: timedelta = timedelta(days=30)
_MAX_BATCH: int = 10_000


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: int
    type: str
    payload: Mapping[str, object]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class StaleScan:
    """`since` es `queued_at` para un escaneo en cola y `started_at` para uno en curso."""

    scan_id: ScanId
    status: ScanStatus
    since: datetime
    requeued_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SweepThresholds:
    """Cuándo se considera atascado un escaneo (AM-308).

    - En cola más de `requeue_after`: se reencola una vez (la caída entre commit y encolado
      no debe perder el escaneo).
    - En cola más de `expire_queued_after`: expira con `QUEUE_EXPIRED`.
    - En curso más de `running_stuck_after`: se abandona. Debe superar el límite duro de la
      tarea, o se abandonarían escaneos sanos.
    """

    running_stuck_after: timedelta
    requeue_after: timedelta = timedelta(minutes=10)
    expire_queued_after: timedelta = timedelta(minutes=60)
    batch_size: int = 100

    def __post_init__(self) -> None:
        if not timedelta(minutes=1) <= self.requeue_after < self.expire_queued_after:
            raise InvalidValueError(
                "requeue_after", "must be >= 1 min and below expire_queued_after"
            )
        if self.running_stuck_after < timedelta(minutes=5):
            raise InvalidValueError("running_stuck_after", "must be at least 5 min")
        if not is_int_in_range(self.batch_size, 1, _MAX_BATCH):
            raise InvalidValueError("batch_size", f"must be in [1, {_MAX_BATCH}]")


@dataclass(frozen=True, slots=True)
class SweepReport:
    requeued: int = 0
    expired_in_queue: int = 0
    abandoned_running: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class RetentionPolicy:
    """Plazos de ADR 0006. Los mínimos protegen funciones que dependen del dato: borrar
    `webhook_deliveries` antes de que GitHub deje de reenviar rompería la idempotencia."""

    webhook_deliveries: timedelta = timedelta(days=30)
    scan_logs: timedelta = timedelta(days=30)
    published_outbox: timedelta = timedelta(days=7)
    scans: timedelta = timedelta(days=365)
    batch_size: int = 1000
    max_batches: int = 50

    def __post_init__(self) -> None:
        for name in ("webhook_deliveries", "scan_logs", "published_outbox"):
            if getattr(self, name) < _MIN_RETENTION:
                raise InvalidValueError(name, "must be at least 1 day")
        if self.scans < _MIN_SCAN_RETENTION:
            raise InvalidValueError("scans", "must be at least 30 days")
        if not is_int_in_range(self.batch_size, 1, _MAX_BATCH):
            raise InvalidValueError("batch_size", f"must be in [1, {_MAX_BATCH}]")
        if not is_int_in_range(self.max_batches, 1, 10_000):
            raise InvalidValueError("max_batches", "must be in [1, 10000]")


@dataclass(frozen=True, slots=True)
class RetentionReport:
    webhook_deliveries: int = 0
    scan_logs: int = 0
    published_outbox: int = 0
    scans: int = 0
    webhook_counters: int = 0


@dataclass(frozen=True, slots=True)
class PurgeCandidate:
    organization_id: OrganizationId
    uninstalled_at: datetime


@dataclass(frozen=True, slots=True)
class PurgeProgress:
    done: bool
    deleted: int
    step: str


@dataclass(frozen=True, slots=True)
class OrganizationPurgeReport:
    purged: int = 0
    in_progress: int = 0
    refused: int = 0
