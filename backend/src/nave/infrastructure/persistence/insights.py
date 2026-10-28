"""Consultas agregadas de lectura por organización (puerto `InsightsQueries`).

Todas filtran por `repositories.organization_id` (o `audit_log.organization_id`) y tienen
tope de filas: nada de lo que el cliente pide amplía ni el ámbito ni el tamaño."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Connection, Select, and_, case, distinct, func, or_, select

from nave.application.dto.export import ExportView
from nave.application.dto.insights import (
    AuditRecord,
    FailedScan,
    FindingFilters,
    FindingRow,
    FixTime,
    OpenBucket,
    PolicyVersion,
    QueueCursor,
    RepositoryLabel,
    RuleStat,
    ScanActivity,
    SummaryPoint,
)
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.identifiers import OrganizationId, RepositoryId, UserId
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.text import is_safe_text
from nave.domain.value_objects.triage_state import TriageState
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.codec import decode_policy
from nave.infrastructure.persistence.mappers import (
    finding_from_row,
    occurrence_from_mapping,
    scan_from_row,
)

_RANK_TO_SEVERITY: Final = {s.rank: s for s in Severity}
_SEVERITY_RANK: Final = case(
    {s.value: s.rank for s in Severity}, value=t.findings.c.severity, else_=0
)
_ACTIVE: Final = tuple(s.value for s in ScanStatus if not s.is_terminal)
_MAX_DETAIL: Final = 512


def _label(row: Any) -> RepositoryLabel:
    return RepositoryLabel(RepositoryId(row.repo_id), row.repo_owner, row.repo_name)


def _repo_columns() -> list[Any]:
    r = t.repositories
    return [r.c.id.label("repo_id"), r.c.owner.label("repo_owner"), r.c.name.label("repo_name")]


def _finding(row: Any) -> Any:
    data = row._mapping
    return finding_from_row({c.name: data[c.name] for c in t.findings.columns})


def _on_default_branch() -> Any:
    s, r = t.scans, t.repositories
    return or_(
        s.c.ref == r.c.default_branch, s.c.ref == func.concat("refs/heads/", r.c.default_branch)
    )


class SqlInsightsQueries:
    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def open_buckets(self, organization_id: OrganizationId) -> Sequence[OpenBucket]:
        f, r = t.findings, t.repositories
        rows = self._conn.execute(
            select(*_repo_columns(), f.c.scanner_kind, f.c.severity, func.count().label("n"))
            .join(r, r.c.id == f.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value,
                f.c.triage_state == TriageState.OPEN.value,
            )
            .group_by(r.c.id, r.c.owner, r.c.name, f.c.scanner_kind, f.c.severity)
        )
        return [
            OpenBucket(_label(row), row.scanner_kind, Severity(row.severity), int(row.n))
            for row in rows
        ]

    def rule_stats(self, organization_id: OrganizationId, *, limit: int) -> Sequence[RuleStat]:
        f, r = t.findings, t.repositories
        is_open = f.c.triage_state == TriageState.OPEN.value
        open_count = func.count().filter(is_open)
        max_rank = func.max(_SEVERITY_RANK)
        rows = self._conn.execute(
            select(
                f.c.rule_id,
                f.c.scanner_kind,
                f.c.category,
                max_rank.label("rank"),
                open_count.label("open"),
                func.count().label("total"),
                func.count(distinct(f.c.repository_id)).label("repos"),
                func.max(f.c.last_seen_at).label("last_seen"),
            )
            .join(r, r.c.id == f.c.repository_id)
            .where(r.c.organization_id == organization_id.value)
            .group_by(f.c.rule_id, f.c.scanner_kind, f.c.category)
            .order_by(open_count.desc(), max_rank.desc(), f.c.rule_id)
            .limit(limit)
        )
        return [
            RuleStat(
                rule_id=row.rule_id,
                scanner=row.scanner_kind,
                category=row.category,
                max_severity=_RANK_TO_SEVERITY[int(row.rank)],
                open=int(row.open),
                total=int(row.total),
                repositories=int(row.repos),
                last_seen_at=row.last_seen,
            )
            for row in rows
        ]

    def fix_times(self, organization_id: OrganizationId) -> Sequence[FixTime]:
        f, r = t.findings, t.repositories
        seconds = func.avg(func.extract("epoch", f.c.last_seen_at - f.c.first_seen_at))
        rows = self._conn.execute(
            select(f.c.severity, func.count().label("n"), seconds.label("mean"))
            .join(r, r.c.id == f.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value,
                f.c.triage_state == TriageState.FIXED.value,
            )
            .group_by(f.c.severity)
        )
        found = [FixTime(Severity(row.severity), int(row.n), float(row.mean or 0)) for row in rows]
        return sorted(found, key=lambda item: item.severity, reverse=True)

    def overdue_counts(
        self, organization_id: OrganizationId, cutoffs: Mapping[Severity, datetime]
    ) -> Mapping[Severity, int]:
        if not cutoffs:
            return {}
        f, r = t.findings, t.repositories
        late = or_(
            *(
                and_(f.c.severity == severity.value, f.c.first_seen_at < cutoff)
                for severity, cutoff in cutoffs.items()
            )
        )
        rows = self._conn.execute(
            select(f.c.severity, func.count().label("n"))
            .join(r, r.c.id == f.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value,
                f.c.triage_state == TriageState.OPEN.value,
                late,
            )
            .group_by(f.c.severity)
        )
        return {Severity(row.severity): int(row.n) for row in rows}

    def open_summaries(
        self, organization_id: OrganizationId, *, since: datetime
    ) -> Sequence[SummaryPoint]:
        s, r = t.scans, t.repositories
        base = and_(
            r.c.organization_id == organization_id.value,
            s.c.status == ScanStatus.COMPLETED.value,
            s.c.summary.is_not(None),
            s.c.finished_at.is_not(None),
            _on_default_branch(),
        )
        within: Select[Any] = (
            select(s).join(r, r.c.id == s.c.repository_id).where(base, s.c.finished_at >= since)
        )
        # El último escaneo anterior a la ventana es el punto de partida de cada repositorio.
        newest_first = func.row_number().over(
            partition_by=s.c.repository_id, order_by=(s.c.finished_at.desc(), s.c.id.desc())
        )
        ranked = (
            select(s, newest_first.label("rank"))
            .join(r, r.c.id == s.c.repository_id)
            .where(base, s.c.finished_at < since)
            .subquery()
        )
        before: Select[Any] = select(ranked).where(ranked.c.rank == 1)
        points: list[SummaryPoint] = []
        for statement in (before, within.order_by(s.c.finished_at).limit(5000)):
            for row in self._conn.execute(statement):
                scan = scan_from_row(row)
                if scan.summary is not None and scan.finished_at is not None:
                    points.append(
                        SummaryPoint(scan.repository_id, scan.finished_at, scan.summary.open)
                    )
        return sorted(points, key=lambda p: p.finished_at)

    def recent_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        s, r = t.scans, t.repositories
        base = select(s, *_repo_columns()).join(r, r.c.id == s.c.repository_id)
        in_org = r.c.organization_id == organization_id.value
        active = self._conn.execute(
            base.where(in_org, s.c.status.in_(_ACTIVE)).order_by(s.c.queued_at.desc()).limit(limit)
        ).all()
        recent = self._conn.execute(
            base.where(in_org).order_by(s.c.queued_at.desc(), s.c.id.desc()).limit(limit)
        ).all()
        seen: set[Any] = set()
        found: list[ScanActivity] = []
        for row in (*active, *recent):
            if row.id in seen:
                continue
            seen.add(row.id)
            found.append(ScanActivity(scan_from_row(row), _label(row)))
        return found

    def accepted_risks(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[FindingRow]:
        f, r, d = t.findings, t.repositories, t.triage_decisions
        justification = (
            select(d.c.justification)
            .where(d.c.finding_id == f.c.id, d.c.state == TriageState.ACCEPTED_RISK.value)
            .order_by(d.c.decided_at.desc(), d.c.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        rows = self._conn.execute(
            select(f, *_repo_columns(), justification.label("justification"))
            .join(r, r.c.id == f.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value,
                f.c.triage_state == TriageState.ACCEPTED_RISK.value,
            )
            .order_by(f.c.triage_expires_at, f.c.id)
            .limit(limit)
        )
        return [FindingRow(_finding(row), _label(row), row.justification) for row in rows]

    def dependency_findings(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[FindingRow]:
        f, r = t.findings, t.repositories
        open_first = case((f.c.triage_state == TriageState.OPEN.value, 0), else_=1)
        rows = self._conn.execute(
            select(f, *_repo_columns())
            .join(r, r.c.id == f.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value,
                f.c.category == FindingCategory.DEPENDENCY.value,
            )
            .order_by(open_first, _SEVERITY_RANK.desc(), f.c.title, f.c.id)
            .limit(limit)
        )
        return [FindingRow(_finding(row), _label(row)) for row in rows]

    def policy_versions(self, organization_id: OrganizationId) -> Sequence[PolicyVersion]:
        p = t.quality_gate_policies
        rows = self._conn.execute(
            select(p)
            .where(p.c.organization_id == organization_id.value)
            .order_by(p.c.version.desc())
            .limit(100)
        )
        return [
            PolicyVersion(
                row.version, bool(row.active), row.created_at, decode_policy(row.document)
            )
            for row in rows
        ]

    def audit_entries(
        self, organization_id: OrganizationId, *, limit: int, before: int | None
    ) -> Sequence[AuditRecord]:
        a, u = t.audit_log, t.users
        statement = (
            select(a, u.c.display_name.label("actor_name"))
            .outerjoin(u, u.c.id == a.c.actor)
            .where(a.c.organization_id == organization_id.value)
        )
        if before is not None:
            statement = statement.where(a.c.id < before)
        rows = self._conn.execute(statement.order_by(a.c.id.desc()).limit(limit))
        return [
            AuditRecord(
                id=int(row.id),
                occurred_at=row.occurred_at,
                action=row.action,
                actor=UserId(row.actor) if row.actor is not None else None,
                actor_name=row.actor_name,
                target_type=row.target_type,
                target_id=row.target_id,
                details=_details(row.details),
            )
            for row in rows
        ]

    # --- ADR 0029 ---------------------------------------------------------------------

    def pull_request_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        s, r = t.scans, t.repositories
        newest = func.row_number().over(
            partition_by=(s.c.repository_id, s.c.pull_request_number),
            order_by=(s.c.queued_at.desc(), s.c.id.desc()),
        )
        ranked = (
            select(s, *_repo_columns(), newest.label("pr_rank"))
            .join(r, r.c.id == s.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value, s.c.pull_request_number.is_not(None)
            )
            .subquery()
        )
        rows = self._conn.execute(
            select(ranked)
            .where(ranked.c.pr_rank == 1)
            .order_by(ranked.c.queued_at.desc(), ranked.c.id.desc())
            .limit(limit)
        )
        return [ScanActivity(scan_from_row(row), _label(row)) for row in rows]

    def triage_queue(
        self,
        organization_id: OrganizationId,
        filters: FindingFilters,
        *,
        after: QueueCursor | None,
        limit: int,
    ) -> Sequence[FindingRow]:
        f, rank = t.findings, _SEVERITY_RANK
        conditions = [f.c.triage_state == TriageState.OPEN.value, *_filters(filters, states=False)]
        if after is not None:
            conditions.append(
                or_(
                    rank < after.rank,
                    and_(
                        rank == after.rank,
                        or_(
                            f.c.first_seen_at > after.first_seen_at,
                            and_(
                                f.c.first_seen_at == after.first_seen_at,
                                f.c.id > after.finding_id.value,
                            ),
                        ),
                    ),
                )
            )
        statement = _located(organization_id).where(*conditions)
        return self._rows(
            statement.order_by(_SEVERITY_RANK.desc(), f.c.first_seen_at, f.c.id).limit(limit)
        )

    def failed_scans(self, organization_id: OrganizationId, *, limit: int) -> Sequence[FailedScan]:
        s, r = t.scans, t.repositories
        later = t.scans.alias("later")
        recovered = (
            select(later.c.id)
            .where(
                later.c.repository_id == s.c.repository_id,
                later.c.ref == s.c.ref,
                later.c.status == ScanStatus.COMPLETED.value,
                later.c.queued_at > s.c.queued_at,
            )
            .exists()
        )
        rows = self._conn.execute(
            select(s, *_repo_columns(), recovered.label("recovered"))
            .join(r, r.c.id == s.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value,
                s.c.status.in_([ScanStatus.FAILED.value, ScanStatus.TIMED_OUT.value]),
            )
            .order_by(s.c.queued_at.desc(), s.c.id.desc())
            .limit(limit)
        )
        return [FailedScan(scan_from_row(row), _label(row), bool(row.recovered)) for row in rows]

    def completed_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        s, r = t.scans, t.repositories
        rows = self._conn.execute(
            select(s, *_repo_columns())
            .join(r, r.c.id == s.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value,
                s.c.status == ScanStatus.COMPLETED.value,
                s.c.summary.is_not(None),
                s.c.finished_at.is_not(None),
            )
            .order_by(s.c.finished_at.desc(), s.c.id.desc())
            .limit(limit)
        )
        return [ScanActivity(scan_from_row(row), _label(row)) for row in rows]

    def export_rows(
        self,
        organization_id: OrganizationId,
        view: ExportView,
        filters: FindingFilters,
        *,
        limit: int,
    ) -> Sequence[FindingRow]:
        f = t.findings
        statement: Select[Any]
        if view is ExportView.TRIAGE:
            statement = (
                _located(organization_id)
                .where(f.c.triage_state == TriageState.OPEN.value, *_filters(filters, states=False))
                .order_by(_SEVERITY_RANK.desc(), f.c.first_seen_at, f.c.id)
            )
        elif view is ExportView.DEPENDENCIES:
            open_first = case((f.c.triage_state == TriageState.OPEN.value, 0), else_=1)
            statement = (
                _located(organization_id)
                .where(f.c.category == FindingCategory.DEPENDENCY.value, *_filters(filters))
                .order_by(open_first, _SEVERITY_RANK.desc(), f.c.title, f.c.id)
            )
        elif view is ExportView.ACCEPTED_RISKS:
            statement = (
                _located(organization_id, justified=True)
                .where(
                    f.c.triage_state == TriageState.ACCEPTED_RISK.value,
                    *_filters(filters, states=False),
                )
                .order_by(f.c.triage_expires_at, f.c.id)
            )
        else:
            if filters.repository_id is None:
                return []
            statement = (
                _located(organization_id)
                .where(*_filters(filters))
                .order_by(_SEVERITY_RANK.desc(), f.c.id)
            )
        return self._rows(statement.limit(limit))

    def _rows(self, statement: Select[Any]) -> Sequence[FindingRow]:
        found = []
        for row in self._conn.execute(statement):
            data = row._mapping
            found.append(
                FindingRow(
                    _finding(row),
                    _label(row),
                    data.get("justification"),
                    _occurrence(data),
                )
            )
        return found


def _filters(filters: FindingFilters, *, states: bool = True) -> list[Any]:
    f = t.findings
    conditions: list[Any] = []
    if filters.severities:
        conditions.append(f.c.severity.in_([s.value for s in filters.severities]))
    if states and filters.states:
        conditions.append(f.c.triage_state.in_([s.value for s in filters.states]))
    if filters.scanner_kinds:
        conditions.append(f.c.scanner_kind.in_([k.value for k in filters.scanner_kinds]))
    if filters.repository_id is not None:
        conditions.append(f.c.repository_id == filters.repository_id.value)
    return conditions


def _located(organization_id: OrganizationId, *, justified: bool = False) -> Select[Any]:
    """Findings de la organización con su repositorio y la ocurrencia del último escaneo
    que los vio (si sigue existiendo: la retención puede haberla purgado)."""
    f, r, o, d = t.findings, t.repositories, t.finding_occurrences, t.triage_decisions
    columns: list[Any] = [f, *_repo_columns(), *_OCCURRENCE_COLUMNS]
    if justified:
        columns.append(
            select(d.c.justification)
            .where(d.c.finding_id == f.c.id, d.c.state == TriageState.ACCEPTED_RISK.value)
            .order_by(d.c.decided_at.desc(), d.c.id.desc())
            .limit(1)
            .scalar_subquery()
            .label("justification")
        )
    return (
        select(*columns)
        .join(r, r.c.id == f.c.repository_id)
        .outerjoin(o, and_(o.c.finding_id == f.c.id, o.c.scan_id == f.c.last_seen_scan_id))
        .where(r.c.organization_id == organization_id.value)
    )


_OCCURRENCE_COLUMNS: Final = (
    t.finding_occurrences.c.scan_id.label("occ_scan_id"),
    t.finding_occurrences.c.path.label("occ_path"),
    t.finding_occurrences.c.start_line.label("occ_start_line"),
    t.finding_occurrences.c.end_line.label("occ_end_line"),
    t.finding_occurrences.c.message.label("occ_message"),
    t.finding_occurrences.c.snippet.label("occ_snippet"),
    t.finding_occurrences.c.is_new.label("occ_is_new"),
)


def _occurrence(data: Mapping[Any, Any]) -> FindingOccurrence | None:
    if data.get("occ_scan_id") is None:
        return None
    return occurrence_from_mapping(
        {
            "scan_id": data["occ_scan_id"],
            "finding_id": data["id"],
            "path": data["occ_path"],
            "start_line": data["occ_start_line"],
            "end_line": data["occ_end_line"],
            "message": data["occ_message"],
            "snippet": data["occ_snippet"],
            "is_new": data["occ_is_new"],
        }
    )


def _details(raw: object) -> Mapping[str, str]:
    """El audit log solo escribe texto corto y seguro; se vuelve a comprobar al leer."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: v
        for k, v in list(raw.items())[:16]
        if isinstance(k, str)
        and isinstance(v, str)
        and len(k) <= _MAX_DETAIL
        and len(v) <= _MAX_DETAIL
        and is_safe_text(k)
        and is_safe_text(v)
    }
