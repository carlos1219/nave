"""API REST de negocio, `/api/v1` (ADR 0017). Adaptadores delgados.

Cada vista: autenticar con la sesión (`SessionGuard`), validar la entrada con esquemas
estrictos, llamar a UN caso de uso con el actor de la sesión —nunca uno que venga del
cliente— y serializar la respuesta con un modelo. La autorización por organización vive en
los casos de uso (CLAUDE.md §12), no aquí."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from flask import Blueprint, Response, g, jsonify, request
from pydantic import BaseModel, ValidationError

from nave.application.dto.export import ExportFile, ExportFormat, ExportView
from nave.application.dto.insights import FindingFilters
from nave.application.dto.views import FindingQuery, PageRequest
from nave.application.services.session_tokens import SESSION_ID_LENGTH
from nave.application.use_cases.browse import (
    GetFinding,
    GetScanVerdict,
    GetScanVerdictQuery,
    ListMembers,
    ListMyOrganizations,
    ListRepositories,
    ListScans,
    ListScansQuery,
)
from nave.application.use_cases.cancel_scan import CancelScan, CancelScanCommand
from nave.application.use_cases.insights import (
    AuditQuery,
    ExportFindings,
    ExportQuery,
    GetOrganizationStats,
    GetPolicyHistory,
    GetScannerReport,
    ListAcceptedRisks,
    ListAuditEntries,
    ListDependencyFindings,
    ListFailedScans,
    ListOrganizationScans,
    ListPullRequests,
    ListRuleCatalog,
    ListTriageQueue,
    OrganizationQuery,
    TriageQueueQuery,
)
from nave.application.use_cases.manage_membership import ManageMembership, ManageMembershipCommand
from nave.application.use_cases.manage_policy import UpdatePolicy, UpdatePolicyCommand
from nave.application.use_cases.my_sessions import (
    ListMySessions,
    RevokeOtherSessions,
    RevokeSession,
    RevokeSessionCommand,
    SessionCommand,
)
from nave.application.use_cases.platform import (
    GetPlatformHealth,
    GetWebhookActivity,
    IsPlatformAdmin,
    ListPlatformOrganizations,
    ListPlatformUsers,
    PlatformMembershipCommand,
    RevokeUserSessions,
    RevokeUserSessionsCommand,
    SetMembershipAsPlatform,
)
from nave.application.use_cases.queries import (
    GetScan,
    GetScanQuery,
    ListFindings,
    ListFindingsQuery,
    ListScanLogs,
    ListScanLogsQuery,
)
from nave.application.use_cases.start_scan import StartScan, StartScanCommand
from nave.application.use_cases.triage_finding import TriageFinding, TriageFindingCommand
from nave.delivery.http import api_models as m
from nave.delivery.http.openapi import API_PREFIX, Operation, Param, Registry
from nave.delivery.http.security import SessionGuard, current_principal
from nave.delivery.http.strict_json import StrictJsonError, loads_strict
from nave.domain.errors import InvalidValueError
from nave.domain.quality_gates import Gate, GateScope, QualityGatePolicy
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import (
    FindingId,
    OrganizationId,
    RepositoryId,
    ScanId,
    UserId,
)
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.triage_state import TriageState

MAX_BODY_BYTES: Final = 16 << 10
MAX_FILTER_VALUES: Final = 8
IDEMPOTENCY_HEADER: Final = "Idempotency-Key"
_DIGITS: Final = "0123456789"
MAX_DIGITS: Final = 12


@dataclass(frozen=True, slots=True)
class RestDependencies:
    guard: SessionGuard
    organizations: ListMyOrganizations
    repositories: ListRepositories
    members: ListMembers
    manage_membership: ManageMembership
    list_scans: ListScans
    get_scan: GetScan
    scan_logs: ListScanLogs
    verdict: GetScanVerdict
    start_scan: StartScan
    cancel_scan: CancelScan
    list_findings: ListFindings
    get_finding: GetFinding
    triage: TriageFinding
    org_stats: GetOrganizationStats
    org_scans: ListOrganizationScans
    accepted_risks: ListAcceptedRisks
    rule_catalog: ListRuleCatalog
    dependencies: ListDependencyFindings
    policy_history: GetPolicyHistory
    audit_log: ListAuditEntries
    pull_requests: ListPullRequests
    triage_queue: ListTriageQueue
    failed_scans: ListFailedScans
    scanner_report: GetScannerReport
    export: ExportFindings
    my_sessions: ListMySessions
    revoke_session: RevokeSession
    revoke_other_sessions: RevokeOtherSessions
    update_policy: UpdatePolicy
    me: IsPlatformAdmin
    platform_users: ListPlatformUsers
    platform_organizations: ListPlatformOrganizations
    platform_membership: SetMembershipAsPlatform
    platform_revoke_sessions: RevokeUserSessions
    platform_health: GetPlatformHealth
    platform_webhooks: GetWebhookActivity


# --- Entrada ----------------------------------------------------------------------------


def _bad(field: str) -> InvalidValueError:
    # Nunca se devuelve el motivo ni el valor recibido: el 400 es siempre el mismo.
    return InvalidValueError(field, "invalid")


def _body[T: BaseModel](model: type[T]) -> T:
    if request.mimetype != "application/json":
        raise _bad("content_type")
    raw = request.get_data(cache=False)
    if len(raw) > MAX_BODY_BYTES:
        raise _bad("body")
    try:
        return model.model_validate(loads_strict(raw))
    except (StrictJsonError, ValidationError) as error:
        raise _bad("body") from error


def _only_params(*allowed: str) -> None:
    if not set(request.args) <= set(allowed):
        raise _bad("query")


def _single(name: str) -> str | None:
    values = request.args.getlist(name)
    if len(values) > 1:
        raise _bad(name)
    return values[0] if values else None


def _int_param(name: str, default: int, low: int, high: int) -> int:
    raw = _single(name)
    if raw is None:
        return default
    if (
        not raw
        or len(raw) > MAX_DIGITS
        or any(c not in _DIGITS for c in raw)
        or not low <= int(raw) <= high
    ):
        raise _bad(name)
    return int(raw)


def _many[T](name: str, parse: Any) -> frozenset[T]:
    values = request.args.getlist(name)
    if len(values) > MAX_FILTER_VALUES:
        raise _bad(name)
    return frozenset(parse(v) for v in values)


def _page() -> PageRequest:
    return PageRequest(limit=_int_param("limit", 50, 1, 200), cursor=_single("cursor"))


def _utc(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise _bad("expires_at") from error
    if parsed.tzinfo is None:
        raise _bad("expires_at")
    return parsed.astimezone(UTC)


def _filters(*, states: bool = False, repository: RepositoryId | None = None) -> FindingFilters:
    """Filtros comunes de la cola y las descargas. Cada valor se valida como en el listado."""
    if repository is None:
        raw = _single("repository_id")
        repository = RepositoryId.parse(raw) if raw is not None else None
    return FindingFilters(
        severities=_many("severity", Severity.parse),
        states=_many("state", TriageState.parse) if states else frozenset(),
        scanner_kinds=_many("scanner", ScannerKind),
        repository_id=repository,
    )


def _limits(items: list[m.SeverityLimitIn]) -> dict[Severity, int]:
    limits = {Severity.parse(i.severity): i.value for i in items}
    if len(limits) != len(items):
        raise _bad("severity")  # una severidad repetida no debe pisar a otra en silencio
    return limits


def _policy(body: m.PolicyRequest) -> QualityGatePolicy:
    """Validación de forma aquí; las reglas (ids únicos, topes) las impone el dominio."""
    return QualityGatePolicy(
        version=1,
        gates=tuple(Gate(g.id, GateScope.parse(g.scope), _limits(g.max)) for g in body.gates),
        sla_days=_limits(body.sla_days),
        fail_on_sla_breach=body.fail_on_sla_breach,
    )


def _reply(model: BaseModel, status: int = 200) -> Response:
    response = jsonify(model.model_dump(mode="json"))
    response.status_code = status
    return response


def _download(file: ExportFile, stem: str) -> Response:
    # Nombre fijo construido aquí: nada del cliente ni de los hallazgos llega a la cabecera.
    response = Response(file.content, status=200, mimetype=file.media_type)
    response.headers["Content-Disposition"] = f'attachment; filename="{stem}.{file.extension}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Nave-Rows"] = str(file.rows)
    return response


# --- Rutas ------------------------------------------------------------------------------

_ORG = Param("organization_id", "Identificador de la organización", "path")
_REPO = Param("repository_id", "Identificador del repositorio", "path")
_SCAN = Param("scan_id", "Identificador del escaneo", "path")
_FINDING = Param("finding_id", "Identificador del finding", "path")
_USER = Param("user_id", "Identificador del usuario", "path")
_LIMIT = Param(
    "limit",
    "Tamaño de página (1-200, por defecto 50)",
    schema={"type": "integer", "minimum": 1, "maximum": 200},
)
_CURSOR = Param("cursor", "Cursor opaco devuelto por la página anterior")
_SEVERITY = Param("severity", "Repetible. CRITICAL, HIGH, MEDIUM, LOW o INFO")
_STATE = Param("state", "Repetible. OPEN, FALSE_POSITIVE, ACCEPTED_RISK o FIXED")
_SCANNER = Param("scanner", "Repetible. Identificador del escáner (`semgrep`, `trivy`)")
_REPO_FILTER = Param("repository_id", "Solo hallazgos de este repositorio de la organización")
_FORMAT = Param(
    "format", "`csv` o `sarif`", required=True, schema={"type": "string", "enum": ["csv", "sarif"]}
)
_EXPORT_TYPES: Final = ("text/csv", "application/sarif+json")
_HEX: Final = "0123456789abcdef"


def rest_blueprint(deps: RestDependencies) -> tuple[Blueprint, Registry]:  # noqa: PLR0915
    blueprint = Blueprint("rest", __name__, url_prefix=API_PREFIX)
    registry = Registry()
    guard = deps.guard

    def route[F: Callable[..., Any]](op: Operation) -> Callable[[F], F]:
        def register(view: F) -> F:
            registry.add(blueprint, op, guard.required(view))
            return view

        return register

    @route(
        Operation(
            "GET",
            "/organizations",
            "listOrganizations",
            "Organizaciones del usuario",
            "organizations",
            success={200: m.OrganizationList},
        )
    )
    def list_organizations() -> Response:
        _only_params()
        found = deps.organizations.execute(current_principal().user_id)
        return _reply(m.OrganizationList(items=[m.OrganizationOut.of(v) for v in found]))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/repositories",
            "listRepositories",
            "Repositorios de una organización",
            "organizations",
            role="VIEWER",
            success={200: m.RepositoryList},
            params=(_ORG,),
        )
    )
    def list_repositories(organization_id: str) -> Response:
        _only_params()
        found = deps.repositories.execute(
            actor=current_principal().user_id, organization_id=OrganizationId.parse(organization_id)
        )
        return _reply(m.RepositoryList(items=[m.RepositoryOut.of(r) for r in found]))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/members",
            "listMembers",
            "Miembros de una organización",
            "organizations",
            role="ADMIN",
            success={200: m.MemberList},
            params=(_ORG,),
        )
    )
    def list_members(organization_id: str) -> Response:
        _only_params()
        found = deps.members.execute(
            actor=current_principal().user_id, organization_id=OrganizationId.parse(organization_id)
        )
        return _reply(m.MemberList(items=[m.MemberOut.of(v) for v in found]))

    @route(
        Operation(
            "PUT",
            "/organizations/{organization_id}/members/{user_id}",
            "setMemberRole",
            "Crea o cambia el rol de un miembro",
            "organizations",
            role="ADMIN",
            request=m.MemberRoleRequest,
            success={204: None},
            params=(_ORG, _USER),
            conflict=True,
        )
    )
    def set_member_role(organization_id: str, user_id: str) -> Response:
        body = _body(m.MemberRoleRequest)
        deps.manage_membership.execute(
            ManageMembershipCommand(
                actor=current_principal().user_id,
                organization_id=OrganizationId.parse(organization_id),
                user=UserId.parse(user_id),
                role=Role.parse(body.role),
            )
        )
        return Response(status=204)

    @route(
        Operation(
            "DELETE",
            "/organizations/{organization_id}/members/{user_id}",
            "removeMember",
            "Retira a un miembro",
            "organizations",
            role="ADMIN",
            success={204: None},
            params=(_ORG, _USER),
            conflict=True,
        )
    )
    def remove_member(organization_id: str, user_id: str) -> Response:
        deps.manage_membership.execute(
            ManageMembershipCommand(
                actor=current_principal().user_id,
                organization_id=OrganizationId.parse(organization_id),
                user=UserId.parse(user_id),
                role=None,
            )
        )
        return Response(status=204)

    @route(
        Operation(
            "GET",
            "/repositories/{repository_id}/scans",
            "listScans",
            "Escaneos de un repositorio, el más reciente primero",
            "scans",
            role="VIEWER",
            success={200: m.ScanPage},
            params=(_REPO, _LIMIT, _CURSOR),
        )
    )
    def list_scans(repository_id: str) -> Response:
        _only_params("limit", "cursor")
        page = deps.list_scans.execute(
            ListScansQuery(
                actor=current_principal().user_id,
                repository_id=RepositoryId.parse(repository_id),
                page=_page(),
            )
        )
        return _reply(
            m.ScanPage(items=[m.ScanOut.of(s) for s in page.items], next_cursor=page.next_cursor)
        )

    @route(
        Operation(
            "POST",
            "/repositories/{repository_id}/scans",
            "startScan",
            "Pide un escaneo manual de un commit",
            "scans",
            role="TRIAGER",
            request=m.StartScanRequest,
            success={200: m.StartScanOut, 202: m.StartScanOut},
            params=(
                _REPO,
                Param(
                    IDEMPOTENCY_HEADER,
                    "UUID elegido por el cliente. Repetir la petición con la misma clave no "
                    "crea otro escaneo (200 en lugar de 202).",
                    "header",
                    required=True,
                ),
            ),
            conflict=True,
        )
    )
    def start_scan(repository_id: str) -> Response:
        key = request.headers.get(IDEMPOTENCY_HEADER)
        if key is None:
            raise _bad("idempotency_key")
        body = _body(m.StartScanRequest)
        result = deps.start_scan.execute(
            StartScanCommand(
                actor=current_principal().user_id,
                repository_id=RepositoryId.parse(repository_id),
                commit=CommitSha(body.commit),
                ref=GitRef(body.ref),
                request_id=key,
            )
        )
        response = _reply(
            m.StartScanOut(scan_id=str(result.scan_id), created=result.created),
            202 if result.created else 200,
        )
        response.headers["Location"] = f"{API_PREFIX}/scans/{result.scan_id}"
        return response

    @route(
        Operation(
            "GET",
            "/scans/{scan_id}",
            "getScan",
            "Estado de un escaneo",
            "scans",
            role="VIEWER",
            success={200: m.ScanOut},
            params=(_SCAN,),
        )
    )
    def get_scan(scan_id: str) -> Response:
        _only_params()
        scan = deps.get_scan.execute(
            GetScanQuery(actor=current_principal().user_id, scan_id=ScanId.parse(scan_id))
        )
        return _reply(m.ScanOut.of(scan))

    @route(
        Operation(
            "POST",
            "/scans/{scan_id}/cancel",
            "cancelScan",
            "Cancela un escaneo",
            "scans",
            role="TRIAGER",
            success={200: m.CancelScanOut},
            params=(_SCAN,),
        )
    )
    def cancel_scan(scan_id: str) -> Response:
        result = deps.cancel_scan.execute(
            CancelScanCommand(actor=current_principal().user_id, scan_id=ScanId.parse(scan_id))
        )
        return _reply(m.CancelScanOut(cancelled=result.cancelled))

    @route(
        Operation(
            "GET",
            "/scans/{scan_id}/logs",
            "listScanLogs",
            "Registro de un escaneo",
            "scans",
            role="VIEWER",
            success={200: m.ScanLogList},
            params=(
                _SCAN,
                Param(
                    "after_seq",
                    "Devuelve solo entradas con `seq` mayor",
                    schema={"type": "integer", "minimum": 0},
                ),
                Param(
                    "limit",
                    "Máximo de entradas (1-500, por defecto 200)",
                    schema={"type": "integer", "minimum": 1, "maximum": 500},
                ),
            ),
        )
    )
    def list_scan_logs(scan_id: str) -> Response:
        _only_params("after_seq", "limit")
        entries = deps.scan_logs.execute(
            ListScanLogsQuery(
                actor=current_principal().user_id,
                scan_id=ScanId.parse(scan_id),
                after_seq=_int_param("after_seq", 0, 0, 2**62),
                limit=_int_param("limit", 200, 1, 500),
            )
        )
        return _reply(m.ScanLogList(items=[m.ScanLogOut.of(e) for e in entries]))

    @route(
        Operation(
            "GET",
            "/scans/{scan_id}/quality-gate",
            "getQualityGate",
            "Resultado de los quality gates de un escaneo",
            "scans",
            role="VIEWER",
            success={200: m.QualityGateOut},
            params=(_SCAN,),
        )
    )
    def get_quality_gate(scan_id: str) -> Response:
        _only_params()
        verdict = deps.verdict.execute(
            GetScanVerdictQuery(actor=current_principal().user_id, scan_id=ScanId.parse(scan_id))
        )
        return _reply(m.QualityGateOut.of(verdict))

    @route(
        Operation(
            "GET",
            "/repositories/{repository_id}/findings",
            "listFindings",
            "Findings de un repositorio",
            "findings",
            role="VIEWER",
            success={200: m.FindingPage},
            params=(
                _REPO,
                Param("severity", "Repetible. CRITICAL, HIGH, MEDIUM, LOW o INFO"),
                Param("state", "Repetible. OPEN, FALSE_POSITIVE, ACCEPTED_RISK o FIXED"),
                Param("scanner", "Repetible. Identificador del escáner (`semgrep`, `trivy`)"),
                Param("seen_in_scan", "Solo los vistos en este escaneo (incluye la ocurrencia)"),
                Param(
                    "only_new",
                    "`true`: solo los nuevos en `seen_in_scan`",
                    schema={"type": "boolean"},
                ),
                _LIMIT,
                _CURSOR,
            ),
        )
    )
    def list_findings(repository_id: str) -> Response:
        _only_params("severity", "state", "scanner", "seen_in_scan", "only_new", "limit", "cursor")
        seen = _single("seen_in_scan")
        only_new = _single("only_new")
        if only_new not in {None, "true", "false"}:
            raise _bad("only_new")
        page = deps.list_findings.execute(
            ListFindingsQuery(
                actor=current_principal().user_id,
                filters=FindingQuery(
                    repository_id=RepositoryId.parse(repository_id),
                    severities=_many("severity", Severity.parse),
                    states=_many("state", TriageState.parse),
                    scanner_kinds=_many("scanner", ScannerKind),
                    seen_in_scan=ScanId.parse(seen) if seen is not None else None,
                    only_new=only_new == "true",
                ),
                page=_page(),
            )
        )
        items = [m.finding_out(v.finding, v.occurrence) for v in page.items]
        return _reply(m.FindingPage(items=items, next_cursor=page.next_cursor))

    @route(
        Operation(
            "GET",
            "/findings/{finding_id}",
            "getFinding",
            "Detalle e historial de triage de un finding",
            "findings",
            role="VIEWER",
            success={200: m.FindingDetailOut},
            params=(_FINDING,),
        )
    )
    def get_finding(finding_id: str) -> Response:
        _only_params()
        detail = deps.get_finding.execute(
            actor=current_principal().user_id, finding_id=FindingId.parse(finding_id)
        )
        return _reply(m.finding_detail_out(detail))

    @route(
        Operation(
            "POST",
            "/findings/{finding_id}/triage",
            "triageFinding",
            "Registra una decisión de triage",
            "findings",
            role="TRIAGER",
            request=m.TriageRequest,
            success={201: m.TriageOut},
            params=(_FINDING,),
            conflict=True,
        )
    )
    def triage_finding(finding_id: str) -> Response:
        body = _body(m.TriageRequest)
        result = deps.triage.execute(
            TriageFindingCommand(
                actor=current_principal().user_id,
                finding_id=FindingId.parse(finding_id),
                state=TriageState.parse(body.state),
                justification=body.justification,
                expires_at=_utc(body.expires_at),
            )
        )
        response = _reply(
            m.TriageOut(decision_id=str(result.decision_id), state=result.state.value), 201
        )
        response.headers["Location"] = f"{API_PREFIX}/findings/{finding_id}"
        return response

    def _org_query(organization_id: str) -> OrganizationQuery:
        _only_params()
        return OrganizationQuery(
            actor=current_principal().user_id,
            organization_id=OrganizationId.parse(organization_id),
        )

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/stats",
            "getOrganizationStats",
            "Estadísticas de hallazgos de una organización",
            "insights",
            role="VIEWER",
            success={200: m.OrganizationStatsOut},
            params=(_ORG,),
        )
    )
    def organization_stats(organization_id: str) -> Response:
        stats = deps.org_stats.execute(_org_query(organization_id))
        return _reply(m.OrganizationStatsOut.of(stats))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/scans",
            "listOrganizationScans",
            "Escaneos en curso y recientes de todos los repositorios de una organización",
            "insights",
            role="VIEWER",
            success={200: m.ScanActivityList},
            params=(_ORG,),
        )
    )
    def organization_scans(organization_id: str) -> Response:
        found = deps.org_scans.execute(_org_query(organization_id))
        return _reply(
            m.ScanActivityList(
                items=[
                    m.ScanActivityOut(
                        scan=m.ScanOut.of(a.scan), repository=m.RepositoryRefOut.of(a.repository)
                    )
                    for a in found
                ]
            )
        )

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/accepted-risks",
            "listAcceptedRisks",
            "Riesgos aceptados de una organización, el que caduca antes primero",
            "insights",
            role="VIEWER",
            success={200: m.FindingRowList},
            params=(_ORG,),
        )
    )
    def accepted_risks(organization_id: str) -> Response:
        found = deps.accepted_risks.execute(_org_query(organization_id))
        return _reply(m.FindingRowList(items=[m.FindingRowOut.of(r) for r in found]))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/rules",
            "listRuleCatalog",
            "Reglas que han producido hallazgos en una organización",
            "insights",
            role="VIEWER",
            success={200: m.RuleCatalogOut},
            params=(_ORG,),
        )
    )
    def rule_catalog(organization_id: str) -> Response:
        found = deps.rule_catalog.execute(_org_query(organization_id))
        return _reply(m.RuleCatalogOut(items=[m.RuleStatOut.of(r) for r in found]))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/dependencies",
            "listDependencyFindings",
            "Dependencias vulnerables (SCA) de una organización",
            "insights",
            role="VIEWER",
            success={200: m.FindingRowList},
            params=(_ORG,),
        )
    )
    def dependency_findings(organization_id: str) -> Response:
        found = deps.dependencies.execute(_org_query(organization_id))
        return _reply(m.FindingRowList(items=[m.FindingRowOut.of(r) for r in found]))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/policy",
            "getPolicyHistory",
            "Versiones de la política de quality gates, la más reciente primero",
            "insights",
            role="VIEWER",
            success={200: m.PolicyHistoryOut},
            params=(_ORG,),
        )
    )
    def policy_history(organization_id: str) -> Response:
        found = deps.policy_history.execute(_org_query(organization_id))
        return _reply(m.PolicyHistoryOut(items=[m.PolicyVersionOut.of(v) for v in found]))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/audit-log",
            "listAuditLog",
            "Registro de auditoría de una organización, lo más reciente primero",
            "insights",
            role="ADMIN",
            success={200: m.AuditPage},
            params=(_ORG, _LIMIT, _CURSOR),
        )
    )
    def audit_log(organization_id: str) -> Response:
        _only_params("limit", "cursor")
        limit = _int_param("limit", 50, 1, 200)
        cursor = _single("cursor")
        if cursor is not None and (
            not cursor or len(cursor) > MAX_DIGITS or any(c not in _DIGITS for c in cursor)
        ):
            raise _bad("cursor")
        found = deps.audit_log.execute(
            AuditQuery(
                actor=current_principal().user_id,
                organization_id=OrganizationId.parse(organization_id),
                limit=limit + 1,
                before=int(cursor) if cursor is not None else None,
            )
        )
        items = list(found[:limit])
        more = len(found) > limit and bool(items)
        return _reply(
            m.AuditPage(
                items=[m.AuditEntryOut.of(e) for e in items],
                next_cursor=str(items[-1].id) if more else None,
            )
        )

    # --- ADR 0029 -------------------------------------------------------------------------

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/pull-requests",
            "listPullRequests",
            "Último escaneo de cada pull request de una organización",
            "insights",
            role="VIEWER",
            success={200: m.ScanActivityList},
            params=(_ORG,),
        )
    )
    def pull_requests(organization_id: str) -> Response:
        found = deps.pull_requests.execute(_org_query(organization_id))
        return _reply(
            m.ScanActivityList(
                items=[
                    m.ScanActivityOut(
                        scan=m.ScanOut.of(a.scan), repository=m.RepositoryRefOut.of(a.repository)
                    )
                    for a in found
                ]
            )
        )

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/triage-queue",
            "listTriageQueue",
            "Hallazgos abiertos de toda la organización, por severidad y antigüedad",
            "insights",
            role="VIEWER",
            success={200: m.FindingRowPage},
            params=(_ORG, _SEVERITY, _SCANNER, _REPO_FILTER, _LIMIT, _CURSOR),
        )
    )
    def triage_queue(organization_id: str) -> Response:
        _only_params("severity", "scanner", "repository_id", "limit", "cursor")
        page = deps.triage_queue.execute(
            TriageQueueQuery(
                actor=current_principal().user_id,
                organization_id=OrganizationId.parse(organization_id),
                filters=_filters(),
                page=_page(),
            )
        )
        return _reply(
            m.FindingRowPage(
                items=[m.FindingRowOut.of(r) for r in page.items], next_cursor=page.next_cursor
            )
        )

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/failed-scans",
            "listFailedScans",
            "Escaneos fallidos o agotados de una organización, el más reciente primero",
            "insights",
            role="VIEWER",
            success={200: m.FailedScanList},
            params=(_ORG,),
        )
    )
    def failed_scans(organization_id: str) -> Response:
        found = deps.failed_scans.execute(_org_query(organization_id))
        return _reply(m.FailedScanList(items=[m.FailedScanOut.of(f) for f in found]))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/scanners",
            "getScannerReport",
            "Versiones de escáner usadas en los escaneos recientes de una organización",
            "insights",
            role="VIEWER",
            success={200: m.ScannerReportOut},
            params=(_ORG,),
        )
    )
    def scanner_report(organization_id: str) -> Response:
        report = deps.scanner_report.execute(_org_query(organization_id))
        return _reply(m.ScannerReportOut.of(report))

    @route(
        Operation(
            "GET",
            "/organizations/{organization_id}/export",
            "exportOrganizationFindings",
            "Descarga una vista de hallazgos de la organización en CSV o SARIF",
            "insights",
            role="VIEWER",
            success={200: None},
            params=(
                _ORG,
                Param(
                    "view",
                    "Vista: `triage`, `dependencies` o `accepted-risks`",
                    required=True,
                    schema={"type": "string", "enum": ["triage", "dependencies", "accepted-risks"]},
                ),
                _FORMAT,
                _SEVERITY,
                _SCANNER,
                _REPO_FILTER,
            ),
            produces=_EXPORT_TYPES,
        )
    )
    def export_organization(organization_id: str) -> Response:
        _only_params("view", "format", "severity", "scanner", "repository_id")
        view = ExportView.parse(_single("view") or "")
        if view is ExportView.REPOSITORY:
            raise _bad("view")
        file = deps.export.execute(
            ExportQuery(
                actor=current_principal().user_id,
                organization_id=OrganizationId.parse(organization_id),
                view=view,
                fmt=ExportFormat.parse(_single("format") or ""),
                filters=_filters(),
            )
        )
        return _download(file, f"nave-{view.value}")

    @route(
        Operation(
            "GET",
            "/repositories/{repository_id}/findings/export",
            "exportRepositoryFindings",
            "Descarga los hallazgos de un repositorio en CSV o SARIF",
            "findings",
            role="VIEWER",
            success={200: None},
            params=(_REPO, _FORMAT, _SEVERITY, _STATE, _SCANNER),
            produces=_EXPORT_TYPES,
        )
    )
    def export_repository(repository_id: str) -> Response:
        _only_params("format", "severity", "state", "scanner")
        filters = _filters(states=True, repository=RepositoryId.parse(repository_id))
        file = deps.export.execute(
            ExportQuery(
                actor=current_principal().user_id,
                view=ExportView.REPOSITORY,
                fmt=ExportFormat.parse(_single("format") or ""),
                filters=filters,
            )
        )
        return _download(file, "nave-findings")

    def _session_command() -> SessionCommand:
        return SessionCommand(principal=current_principal(), token=g.session_token)

    @route(
        Operation(
            "GET",
            "/me/sessions",
            "listMySessions",
            "Sesiones abiertas del usuario",
            "auth",
            success={200: m.SessionList},
        )
    )
    def my_sessions() -> Response:
        _only_params()
        found = deps.my_sessions.execute(_session_command())
        return _reply(m.SessionList(items=[m.SessionInfoOut.of(s) for s in found]))

    @route(
        Operation(
            "DELETE",
            "/me/sessions",
            "revokeOtherSessions",
            "Cierra todas las sesiones del usuario salvo la actual",
            "auth",
            success={200: m.RevokedSessionsOut},
        )
    )
    def revoke_other_sessions() -> Response:
        _only_params()
        revoked = deps.revoke_other_sessions.execute(_session_command())
        return _reply(m.RevokedSessionsOut(revoked=revoked))

    @route(
        Operation(
            "DELETE",
            "/me/sessions/{session_id}",
            "revokeSession",
            "Cierra otra sesión del usuario (la actual se cierra con el logout)",
            "auth",
            success={204: None},
            params=(Param("session_id", "Identificador de la sesión (32 hex)", "path"),),
        )
    )
    def revoke_session(session_id: str) -> Response:
        _only_params()
        if len(session_id) != SESSION_ID_LENGTH or any(c not in _HEX for c in session_id):
            raise _bad("session_id")
        current = _session_command()
        deps.revoke_session.execute(
            RevokeSessionCommand(
                principal=current.principal, token=current.token, session_id=session_id
            )
        )
        return Response(status=204)

    # --- ADR 0030 -------------------------------------------------------------------------

    @route(
        Operation(
            "PUT",
            "/organizations/{organization_id}/policy",
            "updatePolicy",
            "Publica una versión nueva de la política de quality gates",
            "insights",
            role="ADMIN",
            request=m.PolicyRequest,
            success={201: m.PolicyPublishedOut},
            params=(_ORG,),
            conflict=True,
        )
    )
    def update_policy(organization_id: str) -> Response:
        body = _body(m.PolicyRequest)
        version = deps.update_policy.execute(
            UpdatePolicyCommand(
                actor=current_principal().user_id,
                organization_id=OrganizationId.parse(organization_id),
                policy=_policy(body),
            )
        )
        return _reply(m.PolicyPublishedOut(version=version), 201)

    @route(
        Operation(
            "GET",
            "/me",
            "getMe",
            "Usuario de la sesión y si administra la plataforma",
            "auth",
            success={200: m.MeOut},
        )
    )
    def me() -> Response:
        _only_params()
        principal = current_principal()
        return _reply(
            m.MeOut(
                user_id=str(principal.user_id),
                display_name=principal.display_name,
                platform_admin=deps.me.execute(principal.user_id),
            )
        )

    def _platform_op(method: str, path: str, op_id: str, summary: str, **extra: Any) -> Operation:
        return Operation(method, path, op_id, summary, "platform", **extra)

    @route(
        _platform_op(
            "GET",
            "/platform/users",
            "listPlatformUsers",
            "Usuarios de la instalación con sus organizaciones (administración de plataforma)",
            success={200: m.PlatformUserList},
        )
    )
    def platform_users() -> Response:
        _only_params()
        found = deps.platform_users.execute(current_principal().user_id)
        return _reply(m.PlatformUserList(items=[m.PlatformUserOut.of(u) for u in found]))

    @route(
        _platform_op(
            "GET",
            "/platform/organizations",
            "listPlatformOrganizations",
            "Organizaciones de la instalación (administración de plataforma)",
            success={200: m.PlatformOrganizationList},
        )
    )
    def platform_organizations() -> Response:
        _only_params()
        found = deps.platform_organizations.execute(current_principal().user_id)
        return _reply(
            m.PlatformOrganizationList(items=[m.PlatformOrganizationOut.of(o) for o in found])
        )

    @route(
        _platform_op(
            "PUT",
            "/platform/users/{user_id}/memberships/{organization_id}",
            "setPlatformMembership",
            "Da o cambia el rol de un usuario en una organización (administración de plataforma)",
            request=m.MemberRoleRequest,
            success={204: None},
            params=(_USER, _ORG),
            conflict=True,
        )
    )
    def set_platform_membership(user_id: str, organization_id: str) -> Response:
        body = _body(m.MemberRoleRequest)
        deps.platform_membership.execute(
            PlatformMembershipCommand(
                actor=current_principal().user_id,
                user=UserId.parse(user_id),
                organization_id=OrganizationId.parse(organization_id),
                role=Role.parse(body.role),
            )
        )
        return Response(status=204)

    @route(
        _platform_op(
            "DELETE",
            "/platform/users/{user_id}/memberships/{organization_id}",
            "removePlatformMembership",
            "Retira a un usuario de una organización (administración de plataforma)",
            success={204: None},
            params=(_USER, _ORG),
            conflict=True,
        )
    )
    def remove_platform_membership(user_id: str, organization_id: str) -> Response:
        _only_params()
        deps.platform_membership.execute(
            PlatformMembershipCommand(
                actor=current_principal().user_id,
                user=UserId.parse(user_id),
                organization_id=OrganizationId.parse(organization_id),
                role=None,
            )
        )
        return Response(status=204)

    @route(
        _platform_op(
            "DELETE",
            "/platform/users/{user_id}/sessions",
            "revokeUserSessions",
            "Cierra todas las sesiones de un usuario (administración de plataforma)",
            success={200: m.RevokedSessionsOut},
            params=(_USER,),
        )
    )
    def revoke_user_sessions(user_id: str) -> Response:
        _only_params()
        revoked = deps.platform_revoke_sessions.execute(
            RevokeUserSessionsCommand(actor=current_principal().user_id, user=UserId.parse(user_id))
        )
        return _reply(m.RevokedSessionsOut(revoked=revoked))

    @route(
        _platform_op(
            "GET",
            "/platform/health",
            "getPlatformHealth",
            "Salud de los servicios de NAVE (administración de plataforma)",
            success={200: m.HealthOut},
        )
    )
    def platform_health() -> Response:
        _only_params()
        return _reply(m.HealthOut.of(deps.platform_health.execute(current_principal().user_id)))

    @route(
        _platform_op(
            "GET",
            "/platform/webhooks",
            "getWebhookActivity",
            "Entregas de webhooks de GitHub, rechazos y duplicados (administración de plataforma)",
            success={200: m.WebhookActivityOut},
        )
    )
    def platform_webhooks() -> Response:
        _only_params()
        activity = deps.platform_webhooks.execute(current_principal().user_id)
        return _reply(m.WebhookActivityOut.of(activity))

    registry.describe(
        Operation(
            "GET",
            "/auth/login",
            "login",
            "Inicia el login OIDC (redirige al proveedor)",
            "auth",
            success={302: None},
            public=True,
        )
    )
    registry.describe(
        Operation(
            "GET",
            "/auth/callback",
            "loginCallback",
            "Vuelta del proveedor OIDC: crea la sesión y redirige a `/`",
            "auth",
            success={302: None},
            public=True,
            params=(
                Param("state", "Valor `state` devuelto por el proveedor"),
                Param("code", "Código de autorización"),
            ),
        )
    )
    registry.describe(
        Operation("POST", "/auth/logout", "logout", "Cierra la sesión", "auth", success={204: None})
    )
    registry.describe(
        Operation(
            "GET",
            "/session",
            "getSession",
            "Usuario de la sesión y token CSRF",
            "auth",
            success={200: m.SessionOut},
        )
    )
    cached: dict[str, Any] = {}

    @blueprint.get("/openapi.json")
    @guard.required
    def openapi() -> Response:
        if "document" not in cached:
            cached["document"] = registry.document()
        return jsonify(cached["document"])

    return blueprint, registry
