"""Repositorios PostgreSQL. Cada uno trabaja sobre la conexión (y transacción) de su
UnitOfWork; ninguno hace commit por su cuenta."""

import re
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Connection, Select, and_, case, func, insert, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
from nave.application.errors import ConcurrentModificationError, NotFoundError
from nave.application.ports.persistence import PolicyRecord
from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.tenancy import Organization, OrganizationStatus, Repository
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.errors import InvalidValueError
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
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.triage_state import TriageState
from nave.domain.value_objects.trigger import TriggerKey
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.codec import (
    decode_policy,
    decode_verdict,
    encode_policy,
    encode_reasons,
)
from nave.infrastructure.persistence.mappers import (
    decision_from_row,
    decision_values,
    finding_from_row,
    finding_values,
    occurrence_from_mapping,
    occurrence_values,
    organization_from_row,
    repository_from_row,
    scan_from_row,
    scan_values,
)

_ACTIVE_STATUSES: Final = tuple(s.value for s in ScanStatus if not s.is_terminal)
_CURSOR: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)
_BRANCH_PREFIX: Final = "refs/heads/"
_SEVERITY_RANK: Final = case(
    {s.value: s.rank for s in Severity}, value=t.findings.c.severity, else_=-1
)
# Espacios de nombres de pg_advisory_xact_lock(int, int).
SCAN_LOG_LOCK: Final = 1


def advisory_key(value: UUID) -> int:
    """Clave int4 derivada de un UUID. Las colisiones solo serializan de más."""
    return int.from_bytes(value.bytes[:4], "big", signed=True)


MAX_DECISIONS: Final = 200
MAX_MEMBERS: Final = 500
MAX_REPOSITORIES: Final = 1000


class _Repository:
    def __init__(self, connection: Connection) -> None:
        self._conn = connection


class SqlScanRepository(_Repository):
    def get(self, scan_id: ScanId) -> Scan:
        row = self._conn.execute(select(t.scans).where(t.scans.c.id == scan_id.value)).first()
        if row is None:
            raise NotFoundError("scan")
        return scan_from_row(row)

    def add(self, scan: Scan) -> None:
        self._conn.execute(insert(t.scans).values(**scan_values(scan), version=scan.version))

    def save(self, scan: Scan) -> Scan:
        values = scan_values(scan)
        del values["id"]
        row = self._conn.execute(
            update(t.scans)
            .where(t.scans.c.id == scan.id.value, t.scans.c.version == scan.version)
            .values(**values, version=t.scans.c.version + 1)
            .returning(t.scans)
        ).first()
        if row is None:
            self.get(scan.id)  # NotFoundError si no existe
            raise ConcurrentModificationError("scan was modified concurrently")
        return scan_from_row(row)

    def find_by_trigger_key(self, key: TriggerKey) -> Scan | None:
        row = self._conn.execute(select(t.scans).where(t.scans.c.trigger_key == key.value)).first()
        return scan_from_row(row) if row is not None else None

    def find_active_for_ref(self, repository_id: RepositoryId, ref: GitRef) -> Sequence[Scan]:
        rows = self._conn.execute(
            select(t.scans)
            .where(
                t.scans.c.repository_id == repository_id.value,
                t.scans.c.ref == ref.value,
                t.scans.c.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(t.scans.c.queued_at, t.scans.c.id)
        )
        return [scan_from_row(row) for row in rows]

    def list_for_repository(
        self, repository_id: RepositoryId, *, limit: int, before: tuple[datetime, ScanId] | None
    ) -> Sequence[Scan]:
        s = t.scans
        statement = select(s).where(s.c.repository_id == repository_id.value)
        if before is not None:
            at, scan_id = before
            statement = statement.where(tuple_(s.c.queued_at, s.c.id) < tuple_(at, scan_id.value))
        rows = self._conn.execute(
            statement.order_by(s.c.queued_at.desc(), s.c.id.desc()).limit(limit)
        )
        return [scan_from_row(row) for row in rows]


class SqlFindingRepository(_Repository):
    def get(self, finding_id: FindingId) -> Finding:
        row = self._conn.execute(
            select(t.findings).where(t.findings.c.id == finding_id.value)
        ).first()
        if row is None:
            raise NotFoundError("finding")
        return finding_from_row(row)

    def find_by_fingerprints(
        self, repository_id: RepositoryId, fingerprints: Collection[Fingerprint]
    ) -> Mapping[Fingerprint, Finding]:
        wanted = [f.value for f in fingerprints]
        if not wanted:
            return {}
        rows = self._conn.execute(
            select(t.findings).where(
                t.findings.c.repository_id == repository_id.value,
                t.findings.c.fingerprint.in_(wanted),
            )
        )
        found = (finding_from_row(row) for row in rows)
        return {finding.fingerprint: finding for finding in found}

    def add(self, finding: Finding) -> None:
        self._conn.execute(
            insert(t.findings).values(**finding_values(finding), version=finding.version)
        )

    def save(self, finding: Finding) -> Finding:
        values = finding_values(finding)
        del values["id"]
        row = self._conn.execute(
            update(t.findings)
            .where(t.findings.c.id == finding.id.value, t.findings.c.version == finding.version)
            .values(**values, version=t.findings.c.version + 1)
            .returning(t.findings)
        ).first()
        if row is None:
            self.get(finding.id)
            raise ConcurrentModificationError("finding was modified concurrently")
        return finding_from_row(row)

    def list_not_seen_in(
        self,
        repository_id: RepositoryId,
        scan_id: ScanId,
        *,
        after: FindingId | None,
        limit: int,
    ) -> Sequence[Finding]:
        statement = (
            select(t.findings)
            .where(
                t.findings.c.repository_id == repository_id.value,
                t.findings.c.last_seen_scan_id != scan_id.value,
            )
            .order_by(t.findings.c.id)
            .limit(limit)
        )
        if after is not None:
            statement = statement.where(t.findings.c.id > after.value)
        rows = self._conn.execute(statement)
        return [finding_from_row(row) for row in rows]

    def record_occurrences(self, occurrences: Sequence[FindingOccurrence]) -> None:
        if not occurrences:
            return
        statement = pg_insert(t.finding_occurrences).values(
            [occurrence_values(o) for o in occurrences]
        )
        # Idempotente: reprocesar un escaneo no duplica ni altera ocurrencias ya escritas.
        self._conn.execute(
            statement.on_conflict_do_nothing(index_elements=["scan_id", "finding_id"])
        )

    def add_decision(self, decision: TriageDecision) -> None:
        self._conn.execute(insert(t.triage_decisions).values(**decision_values(decision)))

    def baseline_fingerprints(self, repository_id: RepositoryId) -> frozenset[Fingerprint]:
        repo = self._conn.execute(
            select(t.repositories.c.default_branch).where(
                t.repositories.c.id == repository_id.value
            )
        ).first()
        if repo is None:
            raise NotFoundError("repository")
        branch = str(repo.default_branch).removeprefix(_BRANCH_PREFIX)
        latest = (
            select(t.scans.c.id)
            .where(
                t.scans.c.repository_id == repository_id.value,
                t.scans.c.status == ScanStatus.COMPLETED.value,
                t.scans.c.ref.in_([branch, _BRANCH_PREFIX + branch]),
            )
            .order_by(t.scans.c.finished_at.desc(), t.scans.c.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        rows = self._conn.execute(
            select(t.findings.c.fingerprint)
            .join(t.finding_occurrences, t.finding_occurrences.c.finding_id == t.findings.c.id)
            .where(t.finding_occurrences.c.scan_id == latest)
        )
        return frozenset(Fingerprint(row.fingerprint) for row in rows)

    def list(self, query: FindingQuery, page: PageRequest) -> Page[FindingView]:
        f, o = t.findings, t.finding_occurrences
        conditions = [f.c.repository_id == query.repository_id.value]
        if query.severities:
            conditions.append(f.c.severity.in_([s.value for s in query.severities]))
        if query.states:
            conditions.append(f.c.triage_state.in_([s.value for s in query.states]))
        if query.scanner_kinds:
            conditions.append(f.c.scanner_kind.in_([k.value for k in query.scanner_kinds]))
        if page.cursor is not None:
            conditions.append(f.c.id > _decode_cursor(page.cursor))
        statement: Select[Any]
        if query.seen_in_scan is None:
            statement = select(f).where(*conditions)
        else:
            join = and_(o.c.finding_id == f.c.id, o.c.scan_id == query.seen_in_scan.value)
            statement = select(f, *_occurrence_columns()).join(o, join).where(*conditions)
            if query.only_new:
                statement = statement.where(o.c.is_new.is_(True))
        rows = list(self._conn.execute(statement.order_by(f.c.id).limit(page.limit + 1)))
        has_more = len(rows) > page.limit
        views = tuple(_view(row) for row in rows[: page.limit])
        cursor = views[-1].finding.id.value.hex if has_more and views else None
        return Page(views, cursor)

    def view_of(self, finding_id: FindingId) -> FindingView:
        f, o = t.findings, t.finding_occurrences
        join = and_(o.c.finding_id == f.c.id, o.c.scan_id == f.c.last_seen_scan_id)
        row = self._conn.execute(
            select(f, *_occurrence_columns()).outerjoin(o, join).where(f.c.id == finding_id.value)
        ).first()
        if row is None:
            raise NotFoundError("finding")
        return _view(row)

    def decisions_of(self, finding_id: FindingId) -> Sequence[TriageDecision]:
        d = t.triage_decisions
        rows = self._conn.execute(
            select(d)
            .where(d.c.finding_id == finding_id.value)
            .order_by(d.c.decided_at.desc(), d.c.id.desc())
            .limit(MAX_DECISIONS)
        )
        return [decision_from_row(row) for row in rows]

    def views_for_scan(
        self, scan_id: ScanId, *, only_new: bool, limit: int
    ) -> Sequence[FindingView]:
        f, o = t.findings, t.finding_occurrences
        statement = (
            select(f, *_occurrence_columns())
            .join(o, o.c.finding_id == f.c.id)
            .where(o.c.scan_id == scan_id.value)
        )
        if only_new:
            statement = statement.where(o.c.is_new.is_(True))
        rows = self._conn.execute(statement.order_by(_SEVERITY_RANK.desc(), f.c.id).limit(limit))
        return [_view(row) for row in rows]

    def open_counts_by_organization(
        self, organization_ids: Collection[OrganizationId]
    ) -> Mapping[OrganizationId, SeverityCounts]:
        if not organization_ids:
            return {}
        f, r = t.findings, t.repositories
        rows = self._conn.execute(
            select(r.c.organization_id.label("key"), f.c.severity, func.count().label("n"))
            .join(r, r.c.id == f.c.repository_id)
            .where(
                r.c.organization_id.in_([o.value for o in organization_ids]),
                f.c.triage_state == TriageState.OPEN.value,
            )
            .group_by(r.c.organization_id, f.c.severity)
        )
        return {OrganizationId(k): v for k, v in _tally(rows).items()}

    def open_counts_by_repository(
        self, organization_id: OrganizationId
    ) -> Mapping[RepositoryId, SeverityCounts]:
        f, r = t.findings, t.repositories
        rows = self._conn.execute(
            select(f.c.repository_id.label("key"), f.c.severity, func.count().label("n"))
            .join(r, r.c.id == f.c.repository_id)
            .where(
                r.c.organization_id == organization_id.value,
                f.c.triage_state == TriageState.OPEN.value,
            )
            .group_by(f.c.repository_id, f.c.severity)
        )
        return {RepositoryId(k): v for k, v in _tally(rows).items()}


def _tally(rows: Any) -> dict[UUID, SeverityCounts]:
    """Filas `(key, severity, n)` → recuentos por clave."""
    tally: dict[UUID, dict[str, int]] = {}
    for row in rows:
        severity = Severity(row.severity)
        tally.setdefault(row.key, {})[severity.value.lower()] = int(row.n)
    return {key: SeverityCounts(**counts) for key, counts in tally.items()}


def _occurrence_columns() -> list[Any]:
    o = t.finding_occurrences
    return [
        o.c.scan_id.label("occ_scan_id"),
        o.c.path.label("occ_path"),
        o.c.start_line.label("occ_start_line"),
        o.c.end_line.label("occ_end_line"),
        o.c.message.label("occ_message"),
        o.c.snippet.label("occ_snippet"),
        o.c.is_new.label("occ_is_new"),
    ]


def _view(row: Any) -> FindingView:
    data = row._mapping
    finding = finding_from_row({c.name: data[c.name] for c in t.findings.columns})
    if data.get("occ_scan_id") is None:
        return FindingView(finding, None)
    occurrence = occurrence_from_mapping(
        {
            "scan_id": data["occ_scan_id"],
            "finding_id": finding.id.value,
            "path": data["occ_path"],
            "start_line": data["occ_start_line"],
            "end_line": data["occ_end_line"],
            "message": data["occ_message"],
            "snippet": data["occ_snippet"],
            "is_new": data["occ_is_new"],
        }
    )
    return FindingView(finding, occurrence)


def _decode_cursor(cursor: str) -> UUID:
    if _CURSOR.fullmatch(cursor) is None:
        raise InvalidValueError("cursor", "is not a valid cursor")
    return UUID(hex=cursor)


class SqlMembershipRepository(_Repository):
    def role_of(self, user: UserId, organization_id: OrganizationId) -> Role | None:
        row = self._conn.execute(
            select(t.memberships.c.role).where(
                t.memberships.c.user_id == user.value,
                t.memberships.c.organization_id == organization_id.value,
            )
        ).first()
        return Role.parse(row.role) if row is not None else None

    def set_role(
        self,
        user: UserId,
        organization_id: OrganizationId,
        role: Role,
        *,
        granted_by: UserId | None,
        at: datetime,
    ) -> None:
        statement = pg_insert(t.memberships).values(
            user_id=user.value,
            organization_id=organization_id.value,
            role=role.value,
            granted_by=granted_by.value if granted_by is not None else None,
            granted_at=at,
        )
        self._conn.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "organization_id"],
                set_={
                    "role": statement.excluded.role,
                    "granted_by": statement.excluded.granted_by,
                    "granted_at": statement.excluded.granted_at,
                },
            )
        )

    def remove(self, user: UserId, organization_id: OrganizationId) -> None:
        self._conn.execute(
            t.memberships.delete().where(
                t.memberships.c.user_id == user.value,
                t.memberships.c.organization_id == organization_id.value,
            )
        )

    def list_for_user(self, user: UserId) -> Sequence[MembershipView]:
        m, o = t.memberships, t.organizations
        rows = self._conn.execute(
            select(o.c.id, o.c.account_login, o.c.status, m.c.role)
            .join(m, m.c.organization_id == o.c.id)
            .where(m.c.user_id == user.value, o.c.status != OrganizationStatus.UNINSTALLED.value)
            .order_by(o.c.account_login, o.c.id)
        )
        return [
            MembershipView(
                organization_id=OrganizationId(row.id),
                account_login=row.account_login,
                status=OrganizationStatus(row.status),
                role=Role.parse(row.role),
            )
            for row in rows
        ]

    def list_members(self, organization_id: OrganizationId) -> Sequence[MemberView]:
        m, u = t.memberships, t.users
        rank = case({Role.ADMIN.value: 0, Role.TRIAGER.value: 1}, value=m.c.role, else_=2)
        rows = self._conn.execute(
            select(u.c.id, u.c.display_name, u.c.email, m.c.role)
            .join(m, m.c.user_id == u.c.id)
            .where(m.c.organization_id == organization_id.value)
            .order_by(rank, u.c.display_name, u.c.id)
            .limit(MAX_MEMBERS)
        )
        return [
            MemberView(
                user_id=UserId(row.id),
                display_name=row.display_name,
                email=row.email,
                role=Role.parse(row.role),
            )
            for row in rows
        ]

    def count_admins(self, organization_id: OrganizationId) -> int:
        count = self._conn.execute(
            select(func.count())
            .select_from(t.memberships)
            .where(
                t.memberships.c.organization_id == organization_id.value,
                t.memberships.c.role == Role.ADMIN.value,
            )
        ).scalar_one()
        return int(count)

    def lock_admins(self, organization_id: OrganizationId) -> int:
        # `FOR UPDATE` no admite agregados: se bloquean las filas y se cuentan aquí. El orden
        # fijo evita interbloqueos entre dos transacciones que compiten por los mismos admins.
        rows = self._conn.execute(
            select(t.memberships.c.user_id)
            .where(
                t.memberships.c.organization_id == organization_id.value,
                t.memberships.c.role == Role.ADMIN.value,
            )
            .order_by(t.memberships.c.user_id)
            .with_for_update()
        ).all()
        return len(rows)


class SqlTenantCatalog(_Repository):
    def list_repositories(self, organization_id: OrganizationId) -> Sequence[Repository]:
        r = t.repositories
        rows = self._conn.execute(
            select(r)
            .where(r.c.organization_id == organization_id.value)
            .order_by(r.c.owner, r.c.name)
            .limit(MAX_REPOSITORIES)
        )
        return [repository_from_row(row) for row in rows]

    def get_repository(self, repository_id: RepositoryId) -> Repository:
        row = self._conn.execute(
            select(t.repositories).where(t.repositories.c.id == repository_id.value)
        ).first()
        if row is None:
            raise NotFoundError("repository")
        return repository_from_row(row)

    def find_repository_by_github_id(self, github_repo_id: int) -> Repository | None:
        row = self._conn.execute(
            select(t.repositories).where(t.repositories.c.github_repo_id == github_repo_id)
        ).first()
        return repository_from_row(row) if row is not None else None

    def get_organization(self, organization_id: OrganizationId) -> Organization:
        row = self._conn.execute(
            select(t.organizations).where(t.organizations.c.id == organization_id.value)
        ).first()
        if row is None:
            raise NotFoundError("organization")
        return organization_from_row(row)


class SqlTenantAdmin(_Repository):
    def find_organization_by_installation(self, installation_id: int) -> Organization | None:
        row = self._conn.execute(
            select(t.organizations).where(
                t.organizations.c.github_installation_id == installation_id
            )
        ).first()
        return organization_from_row(row) if row is not None else None

    def find_organization_by_account(self, account_id: int) -> Organization | None:
        row = self._conn.execute(
            select(t.organizations).where(t.organizations.c.github_account_id == account_id)
        ).first()
        return organization_from_row(row) if row is not None else None

    def find_organizations_by_login(self, login: str) -> Sequence[Organization]:
        rows = self._conn.execute(
            select(t.organizations)
            .where(t.organizations.c.account_login == login)
            .order_by(t.organizations.c.id)
            .limit(10)
        ).all()
        return [organization_from_row(row) for row in rows]

    def add_organization(self, organization: Organization) -> None:
        self._conn.execute(
            insert(t.organizations).values(
                id=organization.id.value,
                github_installation_id=organization.github_installation_id,
                account_login=organization.account_login,
                status=organization.status.value,
                github_account_id=organization.github_account_id,
                uninstalled_at=None,
            )
        )

    def update_organization(
        self, organization: Organization, *, uninstalled_at: datetime | None
    ) -> None:
        result = self._conn.execute(
            update(t.organizations)
            .where(t.organizations.c.id == organization.id.value)
            .values(
                github_installation_id=organization.github_installation_id,
                account_login=organization.account_login,
                status=organization.status.value,
                github_account_id=organization.github_account_id,
                uninstalled_at=uninstalled_at,
            )
        )
        if result.rowcount != 1:
            raise NotFoundError("organization")

    def list_repositories(self, organization_id: OrganizationId) -> Sequence[Repository]:
        rows = self._conn.execute(
            select(t.repositories)
            .where(t.repositories.c.organization_id == organization_id.value)
            .order_by(t.repositories.c.github_repo_id)
        )
        return [repository_from_row(row) for row in rows]

    def add_repository(self, repository: Repository) -> None:
        self._conn.execute(insert(t.repositories).values(**_repository_values(repository)))

    def update_repository(self, repository: Repository) -> None:
        values = _repository_values(repository)
        values.pop("id")
        values.pop("organization_id")  # un repositorio no cambia de inquilino
        result = self._conn.execute(
            update(t.repositories)
            .where(
                t.repositories.c.id == repository.id.value,
                t.repositories.c.organization_id == repository.organization_id.value,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise NotFoundError("repository")

    def list_installation_ids(self) -> Sequence[int]:
        rows = self._conn.execute(
            select(t.organizations.c.github_installation_id)
            .where(t.organizations.c.status != "UNINSTALLED")
            .order_by(t.organizations.c.github_installation_id)
        )
        return [int(row[0]) for row in rows]


def _repository_values(repository: Repository) -> dict[str, Any]:
    return {
        "id": repository.id.value,
        "organization_id": repository.organization_id.value,
        "github_repo_id": repository.github_repo_id,
        "owner": repository.ref.owner,
        "name": repository.ref.name,
        "default_branch": repository.default_branch.value,
        "enabled": repository.enabled,
    }


class SqlScanLogRepository(_Repository):
    def append(self, scan_id: ScanId, level: LogLevel, message: str, at: datetime) -> None:
        # Se valida con el DTO de lectura antes de escribir: nada que no se pueda leer.
        ScanLogEntry(seq=1, at=at, level=level, message=message)
        # Secuencia por escaneo sin huecos visibles: un lock consultivo por escaneo
        # serializa los append concurrentes sin bloquear la fila del escaneo.
        self._conn.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": SCAN_LOG_LOCK, "key": advisory_key(scan_id.value)},
        )
        next_seq: int = self._conn.execute(
            select(func.coalesce(func.max(t.scan_logs.c.seq), 0) + 1).where(
                t.scan_logs.c.scan_id == scan_id.value
            )
        ).scalar_one()
        self._conn.execute(
            insert(t.scan_logs).values(
                scan_id=scan_id.value, seq=next_seq, at=at, level=level.value, message=message
            )
        )

    def list(self, scan_id: ScanId, *, after_seq: int, limit: int) -> Sequence[ScanLogEntry]:
        rows = self._conn.execute(
            select(t.scan_logs)
            .where(t.scan_logs.c.scan_id == scan_id.value, t.scan_logs.c.seq > after_seq)
            .order_by(t.scan_logs.c.seq)
            .limit(limit)
        )
        return [
            ScanLogEntry(seq=row.seq, at=row.at, level=LogLevel(row.level), message=row.message)
            for row in rows
        ]


class SqlWebhookDeliveryRepository(_Repository):
    def exists(self, delivery_id: str) -> bool:
        row = self._conn.execute(
            select(t.webhook_deliveries.c.delivery_id).where(
                t.webhook_deliveries.c.delivery_id == UUID(delivery_id)
            )
        ).first()
        return row is not None

    def add(self, delivery_id: str, event: str, received_at: datetime, outcome: str) -> None:
        self._conn.execute(
            insert(t.webhook_deliveries).values(
                delivery_id=UUID(delivery_id),
                event=event,
                received_at=received_at,
                outcome=outcome,
            )
        )


class SqlPolicyRepository(_Repository):
    def active_policy(self, organization_id: OrganizationId) -> PolicyRecord | None:
        row = self._conn.execute(
            select(t.quality_gate_policies).where(
                t.quality_gate_policies.c.organization_id == organization_id.value,
                t.quality_gate_policies.c.active.is_(True),
            )
        ).first()
        if row is None:
            return None
        return PolicyRecord(
            policy_id=str(row.id), version=row.version, policy=decode_policy(row.document)
        )

    def publish(
        self,
        organization_id: OrganizationId,
        policy: QualityGatePolicy,
        *,
        policy_id: UUID,
        at: datetime,
    ) -> int:
        p = t.quality_gate_policies
        # Se bloquean las versiones de la organización: dos ediciones simultáneas no pueden
        # calcular el mismo número de versión ni dejar dos activas.
        versions = self._conn.execute(
            select(p.c.version)
            .where(p.c.organization_id == organization_id.value)
            .with_for_update()
        ).all()
        version = max((row.version for row in versions), default=0) + 1
        self._conn.execute(
            update(p)
            .where(p.c.organization_id == organization_id.value, p.c.active.is_(True))
            .values(active=False)
        )
        self._conn.execute(
            insert(p).values(
                id=policy_id,
                organization_id=organization_id.value,
                version=version,
                document=encode_policy(policy),
                active=True,
                created_at=at,
            )
        )
        return version

    def record_evaluation(
        self, scan_id: ScanId, policy_id: str | None, verdict: PolicyVerdict
    ) -> None:
        statement = pg_insert(t.policy_evaluations).values(
            scan_id=scan_id.value,
            policy_id=UUID(policy_id) if policy_id is not None else None,
            verdict=verdict.verdict.value,
            reasons=encode_reasons(verdict),
        )
        self._conn.execute(
            statement.on_conflict_do_update(
                index_elements=["scan_id"],
                set_={
                    "policy_id": statement.excluded.policy_id,
                    "verdict": statement.excluded.verdict,
                    "reasons": statement.excluded.reasons,
                },
            )
        )

    def get_evaluation(self, scan_id: ScanId) -> PolicyVerdict | None:
        row = self._conn.execute(
            select(t.policy_evaluations).where(t.policy_evaluations.c.scan_id == scan_id.value)
        ).first()
        return decode_verdict(row.verdict, row.reasons) if row is not None else None
