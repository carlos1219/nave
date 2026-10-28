"""Esquemas de la API REST (ADR 0017). Pydantic es la única fuente de los contratos: de aquí
salen la validación de entrada, la serialización de salida y el documento OpenAPI.

Entrada: `extra="forbid"` y tipos estrictos; un campo desconocido es un error, no se ignora.
Salida: nunca fingerprints, cabeceras de autenticación ni identificadores internos de GitHub.
Los textos de findings (`message`, `snippet`, `title`) proceden de repositorios no confiables:
el cliente debe tratarlos siempre como texto, nunca como HTML (CLAUDE.md §13)."""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from nave.application.dto.auth import SessionView
from nave.application.dto.insights import (
    AuditRecord,
    FailedScan,
    FindingRow,
    OrganizationStats,
    PolicyVersion,
    RepositoryLabel,
    RuleStat,
    ScannerReport,
)
from nave.application.dto.platform import (
    HealthReport,
    PlatformOrganization,
    PlatformUser,
    WebhookActivity,
)
from nave.application.dto.views import (
    MemberView,
    OrganizationOverview,
    RepositoryOverview,
    ScanLogEntry,
)
from nave.application.use_cases.browse import FindingDetail
from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.quality_gates import PolicyVerdict
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.severity import Severity as DomainSeverity
from nave.domain.value_objects.severity_counts import SeverityCounts

type Uuid = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
type Role = Literal["VIEWER", "TRIAGER", "ADMIN"]
type Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
type TriageState = Literal["OPEN", "FALSE_POSITIVE", "ACCEPTED_RISK", "FIXED"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- Entrada ----------------------------------------------------------------------------


class StartScanRequest(_Request):
    commit: StrictStr = Field(pattern=r"^[0-9a-f]{40}$", description="SHA-1 completo del commit.")
    ref: StrictStr = Field(min_length=1, max_length=255, description="Rama o referencia git.")


class TriageRequest(_Request):
    state: TriageState
    justification: StrictStr | None = Field(
        default=None,
        max_length=2000,
        description="Obligatoria para FALSE_POSITIVE y ACCEPTED_RISK (mínimo 10 caracteres).",
    )
    expires_at: StrictStr | None = Field(
        default=None,
        max_length=40,
        description="ISO 8601 con zona horaria. Obligatoria para ACCEPTED_RISK, prohibida si no.",
    )


class MemberRoleRequest(_Request):
    role: Role


class SeverityLimitIn(_Request):
    severity: Severity
    value: int = Field(ge=0, le=1_000_000, strict=True)


class PolicyGateIn(_Request):
    id: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    scope: Literal["NEW_FINDINGS", "ALL_OPEN"]
    max: list[SeverityLimitIn] = Field(min_length=1, max_length=5)


class PolicyRequest(_Request):
    """Política completa: se publica como versión nueva y la anterior queda inactiva."""

    gates: list[PolicyGateIn] = Field(max_length=32)
    sla_days: list[SeverityLimitIn] = Field(max_length=5)
    fail_on_sla_breach: bool = Field(strict=True)


# --- Salida -----------------------------------------------------------------------------


class Problem(_Response):
    """RFC 7807. Nunca incluye texto de la excepción ni eco de la entrada."""

    type: str
    title: str
    status: int
    request_id: str | None


class SessionUser(_Response):
    id: Uuid
    display_name: str | None


class SessionOut(_Response):
    user: SessionUser
    csrf_token: str
    expires_at: datetime


class SeverityCountsOut(_Response):
    critical: int
    high: int
    medium: int
    low: int
    info: int

    @classmethod
    def of(cls, counts: SeverityCounts) -> Self:
        return cls(
            critical=counts.critical,
            high=counts.high,
            medium=counts.medium,
            low=counts.low,
            info=counts.info,
        )


class OrganizationOut(_Response):
    id: Uuid
    login: str
    status: Literal["ACTIVE", "SUSPENDED"]
    role: Role
    open_findings: SeverityCountsOut = Field(
        description="Findings con triage OPEN por severidad, sumando todos sus repositorios."
    )

    @classmethod
    def of(cls, overview: OrganizationOverview) -> Self:
        view = overview.membership
        return cls(
            id=str(view.organization_id),
            login=view.account_login,
            status=view.status.value,  # type: ignore[arg-type]
            role=view.role.value,
            open_findings=SeverityCountsOut.of(overview.open_findings),
        )


class OrganizationList(_Response):
    items: list[OrganizationOut]


class RepositoryOut(_Response):
    id: Uuid
    owner: str
    name: str
    default_branch: str
    enabled: bool
    open_findings: SeverityCountsOut = Field(description="Findings con triage OPEN por severidad.")

    @classmethod
    def of(cls, overview: RepositoryOverview) -> Self:
        repository = overview.repository
        return cls(
            id=str(repository.id),
            owner=repository.ref.owner,
            name=repository.ref.name,
            default_branch=repository.default_branch.value,
            enabled=repository.enabled,
            open_findings=SeverityCountsOut.of(overview.open_findings),
        )


class RepositoryList(_Response):
    items: list[RepositoryOut]


class MemberOut(_Response):
    user_id: Uuid
    display_name: str | None
    email: str | None
    role: Role

    @classmethod
    def of(cls, view: MemberView) -> Self:
        return cls(
            user_id=str(view.user_id),
            display_name=view.display_name,
            email=view.email,
            role=view.role.value,
        )


class MemberList(_Response):
    items: list[MemberOut]


class ToolRunOut(_Response):
    scanner: str
    version: str
    image_digest: str
    findings: int


class ScanSummaryOut(_Response):
    new: SeverityCountsOut
    open: SeverityCountsOut
    verdict: Literal["PASS", "FAIL", "NEUTRAL"]
    truncated: bool
    tools: list[ToolRunOut] = Field(
        description="Escáneres que analizaron el commit. Vacío en escaneos anteriores al ADR 0029."
    )


class StatusReasonOut(_Response):
    code: str
    detail: str


class ScanOut(_Response):
    id: Uuid
    repository_id: Uuid
    commit: str
    ref: str
    trigger: str
    status: str
    pull_request_number: int | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    status_reason: StatusReasonOut | None
    superseded_by: Uuid | None
    summary: ScanSummaryOut | None

    @classmethod
    def of(cls, scan: Scan) -> Self:
        summary = scan.summary
        return cls(
            id=str(scan.id),
            repository_id=str(scan.repository_id),
            commit=scan.commit.value,
            ref=scan.ref.value,
            trigger=scan.trigger.value,
            status=scan.status.value,
            pull_request_number=scan.pull_request_number,
            queued_at=scan.queued_at,
            started_at=scan.started_at,
            finished_at=scan.finished_at,
            status_reason=(
                StatusReasonOut(
                    code=scan.status_reason.code.value, detail=scan.status_reason.detail
                )
                if scan.status_reason
                else None
            ),
            superseded_by=str(scan.superseded_by) if scan.superseded_by else None,
            summary=(
                ScanSummaryOut(
                    new=SeverityCountsOut.of(summary.new),
                    open=SeverityCountsOut.of(summary.open),
                    verdict=summary.verdict.value,
                    truncated=summary.truncated,
                    tools=[
                        ToolRunOut(
                            scanner=t.scanner.value,
                            version=t.version,
                            image_digest=t.image_digest,
                            findings=t.findings,
                        )
                        for t in summary.tools
                    ],
                )
                if summary
                else None
            ),
        )


class ScanPage(_Response):
    items: list[ScanOut]
    next_cursor: str | None


class StartScanOut(_Response):
    scan_id: Uuid
    created: bool


class CancelScanOut(_Response):
    cancelled: bool


class ScanLogOut(_Response):
    seq: int
    at: datetime
    level: Literal["INFO", "WARNING", "ERROR"]
    message: str

    @classmethod
    def of(cls, entry: ScanLogEntry) -> Self:
        return cls(seq=entry.seq, at=entry.at, level=entry.level.value, message=entry.message)


class ScanLogList(_Response):
    items: list[ScanLogOut]


class VerdictReasonOut(_Response):
    kind: Literal["GATE", "SLA", "TRUNCATED"]
    rule: str
    blocking: bool
    severity: Severity | None
    observed: int
    limit: int


class QualityGateOut(_Response):
    evaluated: bool
    verdict: Literal["PASS", "FAIL", "NEUTRAL"] | None
    reasons: list[VerdictReasonOut]

    @classmethod
    def of(cls, verdict: PolicyVerdict | None) -> Self:
        if verdict is None:
            return cls(evaluated=False, verdict=None, reasons=[])
        return cls(
            evaluated=True,
            verdict=verdict.verdict.value,
            reasons=[
                VerdictReasonOut(
                    kind=r.kind.value,
                    rule=r.rule,
                    blocking=r.blocking,
                    severity=r.severity.value if r.severity else None,
                    observed=r.observed,
                    limit=r.limit,
                )
                for r in verdict.reasons
            ],
        )


class LocationOut(_Response):
    path: str
    start_line: int | None
    end_line: int | None


class OccurrenceOut(_Response):
    scan_id: Uuid
    location: LocationOut | None
    message: str
    snippet: str | None
    is_new: bool

    @classmethod
    def of(cls, occurrence: FindingOccurrence) -> Self:
        where = occurrence.location
        return cls(
            scan_id=str(occurrence.scan_id),
            location=(
                LocationOut(
                    path=where.path.value, start_line=where.start_line, end_line=where.end_line
                )
                if where
                else None
            ),
            message=occurrence.message.value,
            snippet=occurrence.snippet.value if occurrence.snippet else None,
            is_new=occurrence.is_new,
        )


class FindingOut(_Response):
    id: Uuid
    repository_id: Uuid
    scanner: str
    category: str
    rule_id: str
    severity: Severity
    title: str
    cwe: list[str]
    cve: list[str]
    triage_state: TriageState
    triage_expires_at: datetime | None
    first_seen_scan_id: Uuid
    last_seen_scan_id: Uuid
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence: OccurrenceOut | None
    """Presente al filtrar por `seen_in_scan` y en el detalle."""


class FindingPage(_Response):
    items: list[FindingOut]
    next_cursor: str | None


class DecisionOut(_Response):
    id: Uuid
    state: TriageState
    decided_by: Uuid | None
    """`null` si la decisión es del sistema (p. ej. FIXED al desaparecer el finding)."""
    decided_at: datetime
    justification: str | None
    expires_at: datetime | None

    @classmethod
    def of(cls, decision: TriageDecision) -> Self:
        return cls(
            id=str(decision.id),
            state=decision.state.value,
            decided_by=str(decision.decided_by) if decision.decided_by else None,
            decided_at=decision.decided_at,
            justification=decision.justification.value if decision.justification else None,
            expires_at=decision.expires_at,
        )


class FindingDetailOut(FindingOut):
    decisions: list[DecisionOut]


def finding_fields(f: Finding, occurrence: FindingOccurrence | None) -> dict[str, Any]:
    """Campos comunes de `FindingOut` a partir de la entidad."""
    return {
        "id": str(f.id),
        "repository_id": str(f.repository_id),
        "scanner": f.scanner_kind.value,
        "category": f.category.value,
        "rule_id": f.rule_id.value,
        "severity": f.severity.value,
        "title": f.title.value,
        "cwe": [c.value for c in f.cwe],
        "cve": [c.value for c in f.cve],
        "triage_state": f.triage_state.value,
        "triage_expires_at": f.triage_expires_at,
        "first_seen_scan_id": str(f.first_seen_scan_id),
        "last_seen_scan_id": str(f.last_seen_scan_id),
        "first_seen_at": f.first_seen_at,
        "last_seen_at": f.last_seen_at,
        "occurrence": OccurrenceOut.of(occurrence) if occurrence else None,
    }


def finding_detail_out(detail: FindingDetail) -> FindingDetailOut:
    fields = finding_fields(detail.view.finding, detail.view.occurrence)
    return FindingDetailOut(**fields, decisions=[DecisionOut.of(d) for d in detail.decisions])


class TriageOut(_Response):
    decision_id: Uuid
    state: TriageState


def finding_out(finding: Finding, occurrence: FindingOccurrence | None) -> FindingOut:
    return FindingOut(**finding_fields(finding, occurrence))


# --- Vistas por organización (estadísticas, actividad, reglas, riesgos, política, auditoría) ---


class RepositoryRefOut(_Response):
    id: Uuid
    owner: str
    name: str

    @classmethod
    def of(cls, label: RepositoryLabel) -> Self:
        return cls(id=str(label.id), owner=label.owner, name=label.name)


class ScannerCountsOut(_Response):
    scanner: str
    open: SeverityCountsOut


class RepositoryCountsOut(_Response):
    repository: RepositoryRefOut
    open: SeverityCountsOut


class RuleStatOut(_Response):
    rule_id: str
    scanner: str
    category: str
    max_severity: Severity
    open: int
    total: int
    repositories: int
    last_seen_at: datetime

    @classmethod
    def of(cls, stat: RuleStat) -> Self:
        return cls(
            rule_id=stat.rule_id,
            scanner=stat.scanner,
            category=stat.category,
            max_severity=stat.max_severity.value,
            open=stat.open,
            total=stat.total,
            repositories=stat.repositories,
            last_seen_at=stat.last_seen_at,
        )


class RuleCatalogOut(_Response):
    items: list[RuleStatOut]


class FixTimeOut(_Response):
    severity: Severity
    fixed: int
    mean_days: float = Field(description="Días medios entre la primera y la última detección.")


class SlaStatusOut(_Response):
    severity: Severity
    days: int
    overdue: int


class TrendPointOut(_Response):
    day: date
    open: SeverityCountsOut


class OrganizationStatsOut(_Response):
    open: SeverityCountsOut
    by_scanner: list[ScannerCountsOut]
    by_repository: list[RepositoryCountsOut]
    top_rules: list[RuleStatOut]
    fix_times: list[FixTimeOut]
    sla: list[SlaStatusOut]
    """Vacío si la organización no tiene política con plazos."""
    trend: list[TrendPointOut]
    """Abiertos al final de cada uno de los últimos 30 días (UTC), según los escaneos
    completados de la rama por defecto."""

    @classmethod
    def of(cls, stats: OrganizationStats) -> Self:
        return cls(
            open=SeverityCountsOut.of(stats.open),
            by_scanner=[
                ScannerCountsOut(scanner=s, open=SeverityCountsOut.of(c))
                for s, c in stats.by_scanner
            ],
            by_repository=[
                RepositoryCountsOut(repository=RepositoryRefOut.of(r), open=SeverityCountsOut.of(c))
                for r, c in stats.by_repository
            ],
            top_rules=[RuleStatOut.of(r) for r in stats.top_rules],
            fix_times=[
                FixTimeOut(
                    severity=f.severity.value,
                    fixed=f.fixed,
                    mean_days=round(f.mean_seconds / 86400, 2),
                )
                for f in stats.fix_times
            ],
            sla=[
                SlaStatusOut(severity=s.severity.value, days=s.days, overdue=s.overdue)
                for s in stats.sla
            ],
            trend=[
                TrendPointOut(day=p.day, open=SeverityCountsOut.of(p.open)) for p in stats.trend
            ],
        )


class ScanActivityOut(_Response):
    scan: ScanOut
    repository: RepositoryRefOut


class ScanActivityList(_Response):
    items: list[ScanActivityOut]


class FindingRowOut(_Response):
    finding: FindingOut
    repository: RepositoryRefOut
    justification: str | None
    """Solo en riesgos aceptados: la última justificación registrada."""

    @classmethod
    def of(cls, row: FindingRow) -> Self:
        return cls(
            finding=finding_out(row.finding, None),
            repository=RepositoryRefOut.of(row.repository),
            justification=row.justification,
        )


class FindingRowList(_Response):
    items: list[FindingRowOut]


class SeverityLimitOut(_Response):
    severity: Severity
    value: int


class PolicyGateOut(_Response):
    id: str
    scope: Literal["NEW_FINDINGS", "ALL_OPEN"]
    max: list[SeverityLimitOut]


class PolicyVersionOut(_Response):
    version: int
    active: bool
    created_at: datetime
    gates: list[PolicyGateOut]
    sla_days: list[SeverityLimitOut]
    fail_on_sla_breach: bool

    @classmethod
    def of(cls, item: PolicyVersion) -> Self:
        policy = item.policy
        return cls(
            version=item.version,
            active=item.active,
            created_at=item.created_at,
            gates=[
                PolicyGateOut(
                    id=gate.id,
                    scope=gate.scope.value,
                    max=_limits(gate.max_by_severity),
                )
                for gate in policy.gates
            ],
            sla_days=_limits(policy.sla_days),
            fail_on_sla_breach=policy.fail_on_sla_breach,
        )


def _limits(raw: Mapping[DomainSeverity, int]) -> list[SeverityLimitOut]:
    return [
        SeverityLimitOut(severity=s.value, value=n)
        for s, n in sorted(raw.items(), key=lambda item: item[0], reverse=True)
    ]


class PolicyHistoryOut(_Response):
    items: list[PolicyVersionOut]


class AuditEntryOut(_Response):
    id: int
    occurred_at: datetime
    action: str
    actor: Uuid | None
    actor_name: str | None
    target_type: str
    target_id: str
    details: dict[str, str]

    @classmethod
    def of(cls, record: AuditRecord) -> Self:
        return cls(
            id=record.id,
            occurred_at=record.occurred_at,
            action=record.action,
            actor=str(record.actor) if record.actor else None,
            actor_name=record.actor_name,
            target_type=record.target_type,
            target_id=record.target_id,
            details=dict(record.details),
        )


class AuditPage(_Response):
    items: list[AuditEntryOut]
    next_cursor: str | None


# --- ADR 0029: PR, cola de triage, fallidos, escáneres y sesiones ---------------------------


class FindingRowPage(_Response):
    items: list[FindingRowOut]
    next_cursor: str | None


class FailedScanOut(_Response):
    scan: ScanOut
    repository: RepositoryRefOut
    recovered: bool = Field(
        description="Un escaneo posterior del mismo repositorio y rama terminó COMPLETED."
    )

    @classmethod
    def of(cls, failed: FailedScan) -> Self:
        return cls(
            scan=ScanOut.of(failed.scan),
            repository=RepositoryRefOut.of(failed.repository),
            recovered=failed.recovered,
        )


class FailedScanList(_Response):
    items: list[FailedScanOut]


class ToolUsageOut(_Response):
    scanner: str
    version: str
    image_digest: str
    scans: int
    first_used_at: datetime
    last_used_at: datetime


class ScannerReportOut(_Response):
    versions: list[ToolUsageOut]
    """Versiones usadas en los últimos 500 escaneos completados."""
    recent: list[ScanActivityOut]
    without_tools: int = Field(
        description="Escaneos de la muestra sin datos de versión (anteriores al ADR 0029)."
    )

    @classmethod
    def of(cls, report: ScannerReport) -> Self:
        return cls(
            versions=[
                ToolUsageOut(
                    scanner=u.scanner,
                    version=u.version,
                    image_digest=u.image_digest,
                    scans=u.scans,
                    first_used_at=u.first_used_at,
                    last_used_at=u.last_used_at,
                )
                for u in report.versions
            ],
            recent=[
                ScanActivityOut(
                    scan=ScanOut.of(a.scan), repository=RepositoryRefOut.of(a.repository)
                )
                for a in report.recent
            ],
            without_tools=report.without_tools,
        )


class SessionInfoOut(_Response):
    id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    """Lo que antes ocurra: caducidad absoluta o por inactividad."""
    current: bool

    @classmethod
    def of(cls, view: SessionView) -> Self:
        return cls(
            id=view.id,
            created_at=view.created_at,
            last_seen_at=view.last_seen_at,
            expires_at=view.expires_at,
            current=view.current,
        )


class SessionList(_Response):
    items: list[SessionInfoOut]


class RevokedSessionsOut(_Response):
    revoked: int


# --- ADR 0030: administración de plataforma y edición de la política -------------------------


class MeOut(_Response):
    user_id: Uuid
    display_name: str | None
    platform_admin: bool = Field(
        description="Solo para mostrar la sección de plataforma; la autorización es del servidor."
    )


class PolicyPublishedOut(_Response):
    version: int


class PlatformMembershipOut(_Response):
    organization_id: Uuid
    login: str
    role: Role


class PlatformUserOut(_Response):
    id: Uuid
    display_name: str | None
    email: str | None
    last_login_at: datetime | None
    live_sessions: int
    platform_admin: bool
    memberships: list[PlatformMembershipOut]

    @classmethod
    def of(cls, user: PlatformUser) -> Self:
        return cls(
            id=str(user.id),
            display_name=user.display_name,
            email=user.email,
            last_login_at=user.last_login_at,
            live_sessions=user.live_sessions,
            platform_admin=user.platform_admin,
            memberships=[
                PlatformMembershipOut(
                    organization_id=str(m.organization_id), login=m.login, role=m.role.value
                )
                for m in user.memberships
            ],
        )


class PlatformUserList(_Response):
    items: list[PlatformUserOut]


class PlatformOrganizationOut(_Response):
    id: Uuid
    login: str
    status: Literal["ACTIVE", "SUSPENDED"]
    members: int
    admins: int

    @classmethod
    def of(cls, org: PlatformOrganization) -> Self:
        return cls(
            id=str(org.id),
            login=org.login,
            status=org.status.value,  # type: ignore[arg-type]
            members=org.members,
            admins=org.admins,
        )


class PlatformOrganizationList(_Response):
    items: list[PlatformOrganizationOut]


class ComponentHealthOut(_Response):
    component: str
    status: Literal["OK", "DEGRADED", "DOWN"]
    detail: str
    latency_ms: int | None
    checked_at: datetime


class HealthOut(_Response):
    components: list[ComponentHealthOut]
    heartbeat_at: datetime | None
    stale: bool = Field(description="Sin latido del worker en los últimos 3 minutos.")

    @classmethod
    def of(cls, report: HealthReport) -> Self:
        return cls(
            components=[
                ComponentHealthOut(
                    component=c.component.value,
                    status=c.status.value,
                    detail=c.detail,
                    latency_ms=c.latency_ms,
                    checked_at=c.checked_at,
                )
                for c in report.components
            ],
            heartbeat_at=report.heartbeat_at,
            stale=report.stale,
        )


class DeliveryOut(_Response):
    delivery_id: Uuid
    event: str
    outcome: str
    received_at: datetime


class OutcomeCountOut(_Response):
    outcome: str
    count: int


class WebhookCounterOut(_Response):
    minute: datetime
    reason: Literal["SIGNATURE", "MEDIA_TYPE", "MALFORMED", "DUPLICATE"]
    count: int


class WebhookActivityOut(_Response):
    recent: list[DeliveryOut]
    outcomes: list[OutcomeCountOut]
    """Entregas válidas de las últimas 24 h por resultado."""
    counters: list[WebhookCounterOut]
    """Rechazos y duplicados de las últimas 24 h por minuto."""

    @classmethod
    def of(cls, activity: WebhookActivity) -> Self:
        return cls(
            recent=[
                DeliveryOut(
                    delivery_id=d.delivery_id,
                    event=d.event,
                    outcome=d.outcome,
                    received_at=d.received_at,
                )
                for d in activity.recent
            ],
            outcomes=[OutcomeCountOut(outcome=o, count=n) for o, n in activity.outcomes],
            counters=[
                WebhookCounterOut(minute=c.minute, reason=c.reason.value, count=c.count)
                for c in activity.counters
            ],
        )
