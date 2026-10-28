"""Puertos de persistencia. Los implementa `infrastructure.persistence` (PostgreSQL)."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from nave.application.dto.audit import AuditEntry
from nave.application.dto.views import (
    FindingQuery,
    FindingView,
    LogLevel,
    MembershipView,
    MemberView,
    Page,
    PageRequest,
    ScanLogEntry,
)
from nave.application.ports.auth import LoginFlowRepository, SessionRepository, UserRepository
from nave.application.ports.insights import InsightsQueries
from nave.application.ports.messaging import EventPublisherPort
from nave.application.ports.platform import HealthStore, PlatformAdminRepository, PlatformQueries
from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.tenancy import Organization, Repository
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.quality_gates import PolicyVerdict, QualityGatePolicy
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import (
    FindingId,
    OrganizationId,
    RepositoryId,
    ScanId,
    UserId,
)
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.trigger import TriggerKey


class ScanRepository(Protocol):
    def get(self, scan_id: ScanId) -> Scan:
        """Lanza `NotFoundError` si no existe."""
        ...

    def add(self, scan: Scan) -> None: ...

    def save(self, scan: Scan) -> Scan:
        """Guarda con bloqueo optimista sobre `scan.version` y devuelve el escaneo con la
        versión nueva. Lanza `ConcurrentModificationError` si otro lo cambió antes."""
        ...

    def find_by_trigger_key(self, key: TriggerKey) -> Scan | None: ...

    def find_active_for_ref(self, repository_id: RepositoryId, ref: GitRef) -> Sequence[Scan]:
        """Escaneos no terminales de esa rama del repositorio."""
        ...

    def list_for_repository(
        self, repository_id: RepositoryId, *, limit: int, before: tuple[datetime, ScanId] | None
    ) -> Sequence[Scan]:
        """Más recientes primero (`queued_at`, `id`), estrictamente anteriores a `before`."""
        ...


class FindingRepository(Protocol):
    def get(self, finding_id: FindingId) -> Finding:
        """Lanza `NotFoundError` si no existe."""
        ...

    def find_by_fingerprints(
        self, repository_id: RepositoryId, fingerprints: Collection[Fingerprint]
    ) -> Mapping[Fingerprint, Finding]: ...

    def add(self, finding: Finding) -> None: ...

    def save(self, finding: Finding) -> Finding:
        """Bloqueo optimista, igual que `ScanRepository.save`."""
        ...

    def list_not_seen_in(
        self,
        repository_id: RepositoryId,
        scan_id: ScanId,
        *,
        after: FindingId | None,
        limit: int,
    ) -> Sequence[Finding]:
        """Una página de findings cuya última observación no es `scan_id`, ordenada por ID
        y a partir de `after` (exclusivo). Paginar acota la memoria (AM-309)."""
        ...

    def record_occurrences(self, occurrences: Sequence[FindingOccurrence]) -> None:
        """Idempotente por `(scan_id, finding_id)`."""
        ...

    def add_decision(self, decision: TriageDecision) -> None:
        """Append-only: nunca se modifica ni se borra una decisión."""
        ...

    def baseline_fingerprints(self, repository_id: RepositoryId) -> frozenset[Fingerprint]:
        """Fingerprints del último escaneo COMPLETED de la rama por defecto."""
        ...

    def list(self, query: FindingQuery, page: PageRequest) -> Page[FindingView]: ...

    def view_of(self, finding_id: FindingId) -> FindingView:
        """El finding con su ocurrencia en el último escaneo que lo vio (si existe).
        Lanza `NotFoundError`."""
        ...

    def decisions_of(self, finding_id: FindingId) -> Sequence[TriageDecision]:
        """Historial de triage, más reciente primero, con tope de 200."""
        ...

    def views_for_scan(
        self, scan_id: ScanId, *, only_new: bool, limit: int
    ) -> Sequence[FindingView]: ...

    def open_counts_by_organization(
        self, organization_ids: Collection[OrganizationId]
    ) -> Mapping[OrganizationId, SeverityCounts]:
        """Findings con triage `OPEN` por severidad de cada organización pedida. Una
        organización sin ninguno puede faltar en el resultado."""
        ...

    def open_counts_by_repository(
        self, organization_id: OrganizationId
    ) -> Mapping[RepositoryId, SeverityCounts]:
        """Igual que `open_counts_by_organization`, por repositorio de una organización."""
        ...


class MembershipRepository(Protocol):
    def role_of(self, user: UserId, organization_id: OrganizationId) -> Role | None: ...

    def set_role(
        self,
        user: UserId,
        organization_id: OrganizationId,
        role: Role,
        *,
        granted_by: UserId | None,
        at: datetime,
    ) -> None: ...

    def remove(self, user: UserId, organization_id: OrganizationId) -> None: ...

    def count_admins(self, organization_id: OrganizationId) -> int: ...

    def lock_admins(self, organization_id: OrganizationId) -> int:
        """Cuenta los administradores **bloqueando** sus filas hasta el fin de la transacción.

        Quien vaya a quitar o degradar a un administrador debe usarlo: sin el bloqueo, dos
        administradores que se retiran mutuamente a la vez ven cada uno «quedan dos» y la
        organización se queda sin ninguno."""
        ...

    def list_for_user(self, user: UserId) -> Sequence[MembershipView]:
        """Organizaciones no desinstaladas del usuario, por login."""
        ...

    def list_members(self, organization_id: OrganizationId) -> Sequence[MemberView]:
        """Hasta 500 miembros, administradores primero."""
        ...


class TenantCatalog(Protocol):
    def get_repository(self, repository_id: RepositoryId) -> Repository:
        """Lanza `NotFoundError` si no existe."""
        ...

    def find_repository_by_github_id(self, github_repo_id: int) -> Repository | None: ...

    def get_organization(self, organization_id: OrganizationId) -> Organization:
        """Lanza `NotFoundError` si no existe."""
        ...

    def list_repositories(self, organization_id: OrganizationId) -> Sequence[Repository]:
        """Hasta 1000 repositorios, por propietario y nombre."""
        ...


class TenantAdmin(Protocol):
    """Altas y cambios de organizaciones y repositorios, solo desde la sincronización con
    GitHub. Separado de `TenantCatalog` (lectura) para que ningún camino de la API lo use."""

    def find_organization_by_installation(self, installation_id: int) -> Organization | None: ...

    def find_organization_by_account(self, account_id: int) -> Organization | None: ...

    def find_organizations_by_login(self, login: str) -> Sequence[Organization]:
        """Todas las que usan o usaron ese login (puede reasignarse); el llamante filtra."""
        ...

    def add_organization(self, organization: Organization) -> None: ...

    def update_organization(
        self, organization: Organization, *, uninstalled_at: datetime | None
    ) -> None:
        """`uninstalled_at` solo con estado UNINSTALLED: inicia el plazo de purga (RR-11)."""
        ...

    def list_repositories(self, organization_id: OrganizationId) -> Sequence[Repository]: ...

    def add_repository(self, repository: Repository) -> None: ...

    def update_repository(self, repository: Repository) -> None: ...

    def list_installation_ids(self) -> Sequence[int]:
        """Instalaciones no desinstaladas."""
        ...


class ScanLogRepository(Protocol):
    def append(self, scan_id: ScanId, level: LogLevel, message: str, at: datetime) -> None:
        """`message` ya saneado. La secuencia la asigna la persistencia."""
        ...

    def list(self, scan_id: ScanId, *, after_seq: int, limit: int) -> Sequence[ScanLogEntry]: ...


class WebhookDeliveryRepository(Protocol):
    def exists(self, delivery_id: str) -> bool: ...

    def add(self, delivery_id: str, event: str, received_at: datetime, outcome: str) -> None:
        """Restricción única sobre `delivery_id`: dos entregas concurrentes iguales
        provocan `ConcurrentModificationError` en una de ellas."""
        ...


@dataclass(frozen=True, slots=True)
class PolicyRecord:
    policy_id: str
    version: int
    policy: QualityGatePolicy


class PolicyRepository(Protocol):
    def active_policy(self, organization_id: OrganizationId) -> PolicyRecord | None: ...

    def publish(
        self,
        organization_id: OrganizationId,
        policy: QualityGatePolicy,
        *,
        policy_id: UUID,
        at: datetime,
    ) -> int:
        """Nueva versión activa; la anterior queda inactiva en la misma transacción.
        Devuelve el número de versión. Nunca reescribe una versión existente."""
        ...

    def record_evaluation(
        self, scan_id: ScanId, policy_id: str | None, verdict: PolicyVerdict
    ) -> None: ...

    def get_evaluation(self, scan_id: ScanId) -> PolicyVerdict | None: ...


class AuditLogPort(Protocol):
    def append(self, entry: AuditEntry) -> None:
        """Append-only, con encadenado de hashes en la implementación (modelo de datos)."""
        ...


class UnitOfWork(Protocol):
    """Una transacción. Sin `commit()` explícito, al salir del contexto se deshace todo."""

    @property
    def scans(self) -> ScanRepository: ...

    @property
    def findings(self) -> FindingRepository: ...

    @property
    def memberships(self) -> MembershipRepository: ...

    @property
    def users(self) -> UserRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def login_flows(self) -> LoginFlowRepository: ...

    @property
    def tenants(self) -> TenantCatalog: ...

    @property
    def tenant_admin(self) -> TenantAdmin: ...

    @property
    def scan_logs(self) -> ScanLogRepository: ...

    @property
    def webhook_deliveries(self) -> WebhookDeliveryRepository: ...

    @property
    def policies(self) -> PolicyRepository: ...

    @property
    def audit(self) -> AuditLogPort: ...

    @property
    def insights(self) -> InsightsQueries: ...

    @property
    def platform_admins(self) -> PlatformAdminRepository: ...

    @property
    def platform(self) -> PlatformQueries: ...

    @property
    def health(self) -> HealthStore: ...

    @property
    def events(self) -> EventPublisherPort: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
