"""Administración de plataforma (ADR 0030): usuarios, salud y webhooks de la instalación."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from nave.domain.entities.tenancy import OrganizationStatus
from nave.domain.errors import InvalidValueError
from nave.domain.validation import is_int_in_range, is_str_with_length
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.text import sanitize_untrusted

MAX_DETAIL: Final = 256


@dataclass(frozen=True, slots=True)
class UserMembership:
    organization_id: OrganizationId
    login: str
    role: Role


@dataclass(frozen=True, slots=True)
class PlatformUser:
    id: UserId
    display_name: str | None
    email: str | None
    last_login_at: datetime | None
    live_sessions: int
    platform_admin: bool
    memberships: tuple[UserMembership, ...]


@dataclass(frozen=True, slots=True)
class PlatformOrganization:
    id: OrganizationId
    login: str
    status: OrganizationStatus
    members: int
    admins: int


class HealthStatus(Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class HealthComponent(Enum):
    """Lista cerrada: nada del exterior decide qué filas existen."""

    DATABASE = "database"
    BROKER = "broker"
    QUEUE_SCAN = "queue-scan"
    QUEUE_REPORT = "queue-report"
    QUEUE_MAINTENANCE = "queue-maintenance"
    SCAN_RUNNER = "scan-runner"
    EGRESS_PROXY = "egress-proxy"
    WORKER = "worker"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    component: HealthComponent
    status: HealthStatus
    detail: str
    checked_at: datetime
    latency_ms: int | None = None

    def __post_init__(self) -> None:
        if not is_str_with_length(self.detail, 0, MAX_DETAIL):
            raise InvalidValueError("detail", f"length must be at most {MAX_DETAIL}")
        if self.latency_ms is not None and not is_int_in_range(self.latency_ms, 0, 3_600_000):
            raise InvalidValueError("latency_ms", "out of range")

    @classmethod
    def of(
        cls,
        component: HealthComponent,
        status: HealthStatus,
        detail: str,
        at: datetime,
        latency_ms: int | None = None,
    ) -> "ComponentHealth":
        """El detalle puede venir de una excepción: se sanea en lugar de rechazarlo."""
        return cls(component, status, sanitize_untrusted(detail, MAX_DETAIL), at, latency_ms)


@dataclass(frozen=True, slots=True)
class HealthReport:
    components: tuple[ComponentHealth, ...]
    heartbeat_at: datetime | None
    """Último latido del worker (lo dispara el scheduler cada minuto)."""
    stale: bool


class WebhookReason(Enum):
    SIGNATURE = "SIGNATURE"
    MEDIA_TYPE = "MEDIA_TYPE"
    MALFORMED = "MALFORMED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    delivery_id: str
    event: str
    outcome: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class CounterBucket:
    minute: datetime
    reason: WebhookReason
    count: int


@dataclass(frozen=True, slots=True)
class WebhookActivity:
    recent: tuple[DeliveryRecord, ...]
    outcomes: tuple[tuple[str, int], ...]
    """Entregas válidas de las últimas 24 h por resultado."""
    counters: tuple[CounterBucket, ...]
    """Rechazos y duplicados de las últimas 24 h, por minuto."""
