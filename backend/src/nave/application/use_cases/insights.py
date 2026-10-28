"""Vistas de lectura por organización (estadísticas, actividad, reglas, riesgos, dependencias,
política y auditoría). Cada caso de uso exige el rol mínimo en la organización antes de
consultar; sin pertenencia, la respuesta es la de «no existe» (AM-106)."""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from nave.application.dto.export import ExportFile, ExportFormat, ExportView
from nave.application.dto.insights import (
    AuditRecord,
    FailedScan,
    FindingFilters,
    FindingRow,
    OpenBucket,
    OrganizationStats,
    PolicyVersion,
    QueueCursor,
    RepositoryLabel,
    RuleStat,
    ScanActivity,
    ScannerReport,
    SlaStatus,
    ToolUsage,
)
from nave.application.dto.views import Page, PageRequest
from nave.application.ports.export import FindingExportPort
from nave.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.application.services.authorization import Authorizer
from nave.application.services.open_trend import build_trend
from nave.application.services.queue_cursor import decode_queue_cursor, encode_queue_cursor
from nave.domain.errors import InvalidValueError
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.severity_counts import SeverityCounts

TREND_DAYS: Final = 30
TOP_RULES: Final = 10
MAX_ROWS: Final = 500
MAX_SCANS: Final = 100
MAX_PULLS: Final = 200
MAX_FAILED: Final = 200
SCANNER_SAMPLE: Final = 500
RECENT_TOOL_SCANS: Final = 20
MAX_EXPORT_ROWS: Final = 10_000


@dataclass(frozen=True, slots=True, kw_only=True)
class OrganizationQuery:
    actor: UserId
    organization_id: OrganizationId


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditQuery:
    actor: UserId
    organization_id: OrganizationId
    limit: int
    before: int | None


class _OrganizationRead:
    """Abre la transacción, autoriza y delega en la consulta concreta."""

    _required: Role = Role.VIEWER
    _target: str = "organization"

    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def _read[T](
        self, actor: UserId, organization_id: OrganizationId, query: Callable[[UnitOfWork], T]
    ) -> T:
        with self._uow_factory() as uow:
            self._authorizer.require(
                uow,
                actor=actor,
                organization_id=organization_id,
                required=self._required,
                target_type=self._target,
                target_id=str(organization_id),
            )
            return query(uow)


class GetOrganizationStats(_OrganizationRead):
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer, clock: Clock
    ) -> None:
        super().__init__(uow_factory=uow_factory, authorizer=authorizer)
        self._clock = clock

    def execute(self, query: OrganizationQuery) -> OrganizationStats:
        now = self._clock.now()
        org = query.organization_id

        def read(uow: UnitOfWork) -> OrganizationStats:
            buckets = uow.insights.open_buckets(org)
            record = uow.policies.active_policy(org)
            sla_days = dict(record.policy.sla_days) if record is not None else {}
            overdue = uow.insights.overdue_counts(
                org, {s: now - timedelta(days=d) for s, d in sla_days.items()}
            )
            since = now - timedelta(days=TREND_DAYS)
            return OrganizationStats(
                open=_counts(buckets),
                by_scanner=_grouped(buckets, lambda b: b.scanner),
                by_repository=_grouped(buckets, lambda b: b.repository),
                top_rules=tuple(uow.insights.rule_stats(org, limit=TOP_RULES)),
                fix_times=tuple(uow.insights.fix_times(org)),
                sla=tuple(
                    SlaStatus(s, d, overdue.get(s, 0))
                    for s, d in sorted(sla_days.items(), key=lambda item: item[0], reverse=True)
                ),
                trend=build_trend(
                    uow.insights.open_summaries(org, since=since),
                    last_day=now.date(),
                    days=TREND_DAYS,
                ),
            )

        return self._read(query.actor, org, read)


def _counts(buckets: Iterable[OpenBucket]) -> SeverityCounts:
    totals: dict[str, int] = defaultdict(int)
    for bucket in buckets:
        totals[bucket.severity.value.lower()] += bucket.count
    return SeverityCounts(**totals)


def _grouped[K: (str, RepositoryLabel)](
    buckets: Sequence[OpenBucket], key: Callable[[OpenBucket], K]
) -> tuple[tuple[K, SeverityCounts], ...]:
    groups: dict[K, list[OpenBucket]] = defaultdict(list)
    for bucket in buckets:
        groups[key(bucket)].append(bucket)
    rows = [(k, _counts(v)) for k, v in groups.items()]
    rows.sort(key=lambda row: (-row[1].total, str(row[0])))
    return tuple(rows)


class ListOrganizationScans(_OrganizationRead):
    def execute(self, query: OrganizationQuery) -> Sequence[ScanActivity]:
        org = query.organization_id
        return self._read(
            query.actor, org, lambda uow: uow.insights.recent_scans(org, limit=MAX_SCANS)
        )


class ListAcceptedRisks(_OrganizationRead):
    def execute(self, query: OrganizationQuery) -> Sequence[FindingRow]:
        org = query.organization_id
        return self._read(
            query.actor, org, lambda uow: uow.insights.accepted_risks(org, limit=MAX_ROWS)
        )


class ListRuleCatalog(_OrganizationRead):
    def execute(self, query: OrganizationQuery) -> Sequence[RuleStat]:
        org = query.organization_id
        return self._read(
            query.actor, org, lambda uow: uow.insights.rule_stats(org, limit=MAX_ROWS)
        )


class ListDependencyFindings(_OrganizationRead):
    def execute(self, query: OrganizationQuery) -> Sequence[FindingRow]:
        org = query.organization_id
        return self._read(
            query.actor, org, lambda uow: uow.insights.dependency_findings(org, limit=MAX_ROWS)
        )


class GetPolicyHistory(_OrganizationRead):
    def execute(self, query: OrganizationQuery) -> Sequence[PolicyVersion]:
        org = query.organization_id
        return self._read(query.actor, org, lambda uow: uow.insights.policy_versions(org))


class ListAuditEntries(_OrganizationRead):
    """Solo ADMIN: el registro muestra quién hizo qué y quién intentó lo que no podía."""

    _required = Role.ADMIN
    _target = "audit_log"

    def execute(self, query: AuditQuery) -> Sequence[AuditRecord]:
        org = query.organization_id
        return self._read(
            query.actor,
            org,
            lambda uow: uow.insights.audit_entries(org, limit=query.limit, before=query.before),
        )


# --- ADR 0029: PR, cola de triage, fallidos, escáneres y exportación ---------------------


class ListPullRequests(_OrganizationRead):
    def execute(self, query: OrganizationQuery) -> Sequence[ScanActivity]:
        org = query.organization_id
        return self._read(
            query.actor, org, lambda uow: uow.insights.pull_request_scans(org, limit=MAX_PULLS)
        )


class ListFailedScans(_OrganizationRead):
    def execute(self, query: OrganizationQuery) -> Sequence[FailedScan]:
        org = query.organization_id
        return self._read(
            query.actor, org, lambda uow: uow.insights.failed_scans(org, limit=MAX_FAILED)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TriageQueueQuery:
    actor: UserId
    organization_id: OrganizationId
    filters: FindingFilters
    page: PageRequest


class ListTriageQueue(_OrganizationRead):
    def execute(self, query: TriageQueueQuery) -> Page[FindingRow]:
        org = query.organization_id
        after = decode_queue_cursor(query.page.cursor) if query.page.cursor else None
        limit = query.page.limit
        rows = self._read(
            query.actor,
            org,
            lambda uow: uow.insights.triage_queue(org, query.filters, after=after, limit=limit + 1),
        )
        items = tuple(rows[:limit])
        if len(rows) <= limit or not items:
            return Page(items)
        last = items[-1].finding
        cursor = QueueCursor(
            rank=last.severity.rank, first_seen_at=last.first_seen_at, finding_id=last.id
        )
        return Page(items, encode_queue_cursor(cursor))


class GetScannerReport(_OrganizationRead):
    def execute(self, query: OrganizationQuery) -> ScannerReport:
        org = query.organization_id
        scans = self._read(
            query.actor, org, lambda uow: uow.insights.completed_scans(org, limit=SCANNER_SAMPLE)
        )
        return ScannerReport(
            versions=_tool_usage(scans),
            recent=tuple(scans[:RECENT_TOOL_SCANS]),
            without_tools=sum(1 for a in scans if a.scan.summary and not a.scan.summary.tools),
        )


def _tool_usage(scans: Sequence[ScanActivity]) -> tuple[ToolUsage, ...]:
    seen: dict[tuple[str, str, str], list[datetime]] = defaultdict(list)
    for activity in scans:
        scan = activity.scan
        if scan.summary is None or scan.finished_at is None:
            continue
        for tool in scan.summary.tools:
            key = (tool.scanner.value, tool.version, tool.image_digest)
            seen[key].append(scan.finished_at)
    usage = [
        ToolUsage(
            scanner=scanner,
            version=version,
            image_digest=digest,
            scans=len(times),
            first_used_at=min(times),
            last_used_at=max(times),
        )
        for (scanner, version, digest), times in seen.items()
    ]
    usage.sort(key=lambda u: (u.scanner, -u.last_used_at.timestamp(), u.version))
    return tuple(usage)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExportQuery:
    actor: UserId
    view: ExportView
    fmt: ExportFormat
    filters: FindingFilters
    organization_id: OrganizationId | None = None
    """Para `REPOSITORY` se deduce de `filters.repository_id`."""

    def __post_init__(self) -> None:
        by_repository = self.view is ExportView.REPOSITORY
        if by_repository != (self.organization_id is None) or (
            by_repository and self.filters.repository_id is None
        ):
            raise InvalidValueError("export", "repository exports need only a repository")


class ExportFindings:
    """Descarga de una vista de hallazgos. Misma autorización que la vista: VIEWER."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        authorizer: Authorizer,
        exporter: FindingExportPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer
        self._exporter = exporter

    def execute(self, query: ExportQuery) -> ExportFile:
        with self._uow_factory() as uow:
            if query.organization_id is not None:
                org = query.organization_id
                target, target_id = "organization", str(org)
            else:
                assert query.filters.repository_id is not None  # noqa: S101 - __post_init__
                repository = uow.tenants.get_repository(query.filters.repository_id)
                org = repository.organization_id
                target, target_id = "repository", str(repository.id)
            self._authorizer.require(
                uow,
                actor=query.actor,
                organization_id=org,
                required=Role.VIEWER,
                target_type=target,
                target_id=target_id,
            )
            rows = uow.insights.export_rows(org, query.view, query.filters, limit=MAX_EXPORT_ROWS)
        return self._exporter.render(rows, query.fmt)
