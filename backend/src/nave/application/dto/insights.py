"""Vistas de lectura agregadas por organización: estadísticas, actividad, reglas, riesgos,
dependencias, política y auditoría. Solo datos ya validados por el dominio o acotados aquí."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.quality_gates import QualityGatePolicy
from nave.domain.value_objects.identifiers import FindingId, RepositoryId, UserId
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.triage_state import TriageState


@dataclass(frozen=True, slots=True)
class RepositoryLabel:
    id: RepositoryId
    owner: str
    name: str


@dataclass(frozen=True, slots=True)
class OpenBucket:
    """Findings con triage `OPEN` de un repositorio, escáner y severidad."""

    repository: RepositoryLabel
    scanner: str
    severity: Severity
    count: int


@dataclass(frozen=True, slots=True)
class RuleStat:
    rule_id: str
    scanner: str
    category: str
    max_severity: Severity
    open: int
    total: int
    repositories: int
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class FixTime:
    """Findings corregidos de una severidad y su tiempo medio entre la primera y la última
    detección (el escaneo siguiente ya no los vio)."""

    severity: Severity
    fixed: int
    mean_seconds: float


@dataclass(frozen=True, slots=True)
class SummaryPoint:
    """Recuento de abiertos que dejó un escaneo completado de la rama por defecto."""

    repository_id: RepositoryId
    finished_at: datetime
    open: SeverityCounts


@dataclass(frozen=True, slots=True)
class SlaStatus:
    severity: Severity
    days: int
    overdue: int


@dataclass(frozen=True, slots=True)
class TrendPoint:
    day: date
    open: SeverityCounts


@dataclass(frozen=True, slots=True)
class OrganizationStats:
    open: SeverityCounts
    by_scanner: tuple[tuple[str, SeverityCounts], ...]
    by_repository: tuple[tuple[RepositoryLabel, SeverityCounts], ...]
    top_rules: tuple[RuleStat, ...]
    fix_times: tuple[FixTime, ...]
    sla: tuple[SlaStatus, ...]
    trend: tuple[TrendPoint, ...]


@dataclass(frozen=True, slots=True)
class ScanActivity:
    scan: Scan
    repository: RepositoryLabel


@dataclass(frozen=True, slots=True)
class FindingRow:
    """Un finding con su repositorio y, para riesgos aceptados, la última justificación.

    `occurrence` es la del último escaneo que lo vio, si la consulta la pide (exportación)."""

    finding: Finding
    repository: RepositoryLabel
    justification: str | None = None
    occurrence: FindingOccurrence | None = None


@dataclass(frozen=True, slots=True)
class FailedScan:
    """Escaneo `FAILED` o `TIMED_OUT`. `recovered`: un escaneo posterior del mismo
    repositorio y rama terminó `COMPLETED`."""

    scan: Scan
    repository: RepositoryLabel
    recovered: bool


@dataclass(frozen=True, slots=True)
class ToolUsage:
    """Una versión de un escáner (escáner, versión, imagen) y en cuántos escaneos se usó."""

    scanner: str
    version: str
    image_digest: str
    scans: int
    first_used_at: datetime
    last_used_at: datetime


@dataclass(frozen=True, slots=True)
class ScannerReport:
    versions: tuple[ToolUsage, ...]
    recent: tuple[ScanActivity, ...]
    """Escaneos completados más recientes, con sus ejecuciones en `scan.summary.tools`."""
    without_tools: int
    """Escaneos completados de la muestra sin el dato (anteriores al ADR 0029)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class QueueCursor:
    """Posición en la cola de triage: severidad descendente, antigüedad y id ascendentes."""

    rank: int
    first_seen_at: datetime
    finding_id: FindingId


@dataclass(frozen=True, slots=True, kw_only=True)
class FindingFilters:
    severities: frozenset[Severity] = field(default_factory=frozenset)
    states: frozenset[TriageState] = field(default_factory=frozenset)
    scanner_kinds: frozenset[ScannerKind] = field(default_factory=frozenset)
    repository_id: RepositoryId | None = None


@dataclass(frozen=True, slots=True)
class PolicyVersion:
    version: int
    active: bool
    created_at: datetime
    policy: QualityGatePolicy


@dataclass(frozen=True, slots=True)
class AuditRecord:
    id: int
    occurred_at: datetime
    action: str
    actor: UserId | None
    actor_name: str | None
    target_type: str
    target_id: str
    details: Mapping[str, str]
