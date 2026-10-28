"""Implementaciones en memoria de los puertos, para tests de aplicación.

Respetan los contratos que importan a los casos de uso: bloqueo optimista,
transacciones con rollback, idempotencia de ocurrencias y escritura de eventos
en el outbox solo al hacer commit.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID

from nave.application.dto.audit import AuditEntry
from nave.application.dto.auth import LoginFlow, OidcIdentity, SessionRecord, UserSummary
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
from nave.application.dto.platform import (
    ComponentHealth,
    CounterBucket,
    DeliveryRecord,
    HealthComponent,
    PlatformOrganization,
    PlatformUser,
    UserMembership,
    WebhookReason,
)
from nave.application.dto.scanning import FetchLimits, ScanLimits, ScannerOutput
from nave.application.dto.vcs import PrCommentBody, VcsTarget
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
from nave.application.dto.workspace import WorkspaceHandle
from nave.application.errors import ConcurrentModificationError, NotFoundError
from nave.application.ports.messaging import ScanStep
from nave.application.ports.persistence import PolicyRecord
from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.tenancy import Organization, OrganizationStatus, Repository
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.events import DomainEvent
from nave.domain.quality_gates import (
    FindingSnapshot,
    PolicyVerdict,
    QualityGatePolicy,
    evaluate_quality_gates,
)
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.finding_category import FindingCategory
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
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.triage_state import TriageState
from nave.domain.value_objects.trigger import TriggerKey


@dataclass
class State:
    scans: dict[ScanId, Scan] = field(default_factory=dict)
    findings: dict[FindingId, Finding] = field(default_factory=dict)
    occurrences: dict[tuple[ScanId, FindingId], FindingOccurrence] = field(default_factory=dict)
    decisions: list[TriageDecision] = field(default_factory=list)
    memberships: dict[tuple[UserId, OrganizationId], Role] = field(default_factory=dict)
    organizations: dict[OrganizationId, Organization] = field(default_factory=dict)
    repositories: dict[RepositoryId, Repository] = field(default_factory=dict)
    uninstalled_at: dict[OrganizationId, datetime] = field(default_factory=dict)
    users: dict[UserId, UserSummary] = field(default_factory=dict)
    sessions: dict[bytes, SessionRecord] = field(default_factory=dict)
    login_flows: dict[bytes, LoginFlow] = field(default_factory=dict)
    logs: dict[ScanId, list[ScanLogEntry]] = field(default_factory=dict)
    deliveries: dict[str, str] = field(default_factory=dict)
    policies: dict[OrganizationId, PolicyRecord] = field(default_factory=dict)
    evaluations: dict[ScanId, PolicyVerdict] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    outbox: list[DomainEvent] = field(default_factory=list)
    admin_locks: list[OrganizationId] = field(default_factory=list)
    platform_admins: dict[UserId, datetime] = field(default_factory=dict)
    health: dict[HealthComponent, ComponentHealth] = field(default_factory=dict)
    delivery_log: list[DeliveryRecord] = field(default_factory=list)
    webhook_counters: dict[tuple[datetime, WebhookReason], int] = field(default_factory=dict)

    def clone(self) -> State:
        # Los valores son inmutables: basta copiar los contenedores.
        copied = State(**{f.name: copy.copy(getattr(self, f.name)) for f in fields(self)})
        copied.logs = {scan_id: list(entries) for scan_id, entries in self.logs.items()}
        return copied


class Store:
    """Estado "persistido". Cada UnitOfWork trabaja sobre una copia."""

    def __init__(self) -> None:
        self.state = State()
        self.commits = 0

    def uow(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self)


class _Repo:
    def __init__(self, state: State) -> None:
        self._s = state


class InMemoryScans(_Repo):
    def get(self, scan_id: ScanId) -> Scan:
        try:
            return self._s.scans[scan_id]
        except KeyError:
            raise NotFoundError("scan") from None

    def _check_superseded_by(self, scan: Scan) -> None:
        # Réplica de `fk_scans_superseded_by_scans`: PostgreSQL la comprueba en cada
        # sentencia y la infraestructura la traduce a `NotFoundError` (23503).
        if scan.superseded_by is not None and scan.superseded_by not in self._s.scans:
            raise NotFoundError("related resource")

    def add(self, scan: Scan) -> None:
        if scan.id in self._s.scans:
            raise ConcurrentModificationError("scan already exists")
        self._check_superseded_by(scan)
        self._s.scans[scan.id] = scan

    def save(self, scan: Scan) -> Scan:
        stored = self.get(scan.id)
        if stored.version != scan.version:
            raise ConcurrentModificationError("scan was modified concurrently")
        self._check_superseded_by(scan)
        saved = replace(scan, version=scan.version + 1)
        self._s.scans[scan.id] = saved
        return saved

    def list_for_repository(
        self, repository_id: RepositoryId, *, limit: int, before: tuple[datetime, ScanId] | None
    ) -> Sequence[Scan]:
        scans = [s for s in self._s.scans.values() if s.repository_id == repository_id]
        scans.sort(key=lambda s: (s.queued_at, s.id.value), reverse=True)
        if before is not None:
            at, scan_id = before
            scans = [s for s in scans if (s.queued_at, s.id.value) < (at, scan_id.value)]
        return scans[:limit]

    def find_by_trigger_key(self, key: TriggerKey) -> Scan | None:
        return next((s for s in self._s.scans.values() if s.trigger_key == key), None)

    def find_active_for_ref(self, repository_id: RepositoryId, ref: GitRef) -> Sequence[Scan]:
        return [
            s
            for s in self._s.scans.values()
            if s.repository_id == repository_id and s.ref == ref and not s.is_terminal
        ]


class InMemoryFindings(_Repo):
    def get(self, finding_id: FindingId) -> Finding:
        try:
            return self._s.findings[finding_id]
        except KeyError:
            raise NotFoundError("finding") from None

    def find_by_fingerprints(
        self, repository_id: RepositoryId, fingerprints: Collection[Fingerprint]
    ) -> Mapping[Fingerprint, Finding]:
        wanted = set(fingerprints)
        return {
            f.fingerprint: f
            for f in self._s.findings.values()
            if f.repository_id == repository_id and f.fingerprint in wanted
        }

    def add(self, finding: Finding) -> None:
        duplicate = self.find_by_fingerprints(finding.repository_id, [finding.fingerprint])
        if finding.id in self._s.findings or duplicate:
            raise ConcurrentModificationError("finding already exists")
        self._s.findings[finding.id] = finding

    def save(self, finding: Finding) -> Finding:
        stored = self.get(finding.id)
        if stored.version != finding.version:
            raise ConcurrentModificationError("finding was modified concurrently")
        saved = replace(finding, version=finding.version + 1)
        self._s.findings[finding.id] = saved
        return saved

    def list_not_seen_in(
        self,
        repository_id: RepositoryId,
        scan_id: ScanId,
        *,
        after: FindingId | None,
        limit: int,
    ) -> Sequence[Finding]:
        candidates = sorted(
            (
                f
                for f in self._s.findings.values()
                if f.repository_id == repository_id
                and f.last_seen_scan_id != scan_id
                and (after is None or f.id.value > after.value)
            ),
            key=lambda f: f.id.value,
        )
        return candidates[:limit]

    def record_occurrences(self, occurrences: Sequence[FindingOccurrence]) -> None:
        for occurrence in occurrences:
            self._s.occurrences[(occurrence.scan_id, occurrence.finding_id)] = occurrence

    def add_decision(self, decision: TriageDecision) -> None:
        self._s.decisions.append(decision)

    def baseline_fingerprints(self, repository_id: RepositoryId) -> frozenset[Fingerprint]:
        repository = self._s.repositories[repository_id]
        candidates = [
            s
            for s in self._s.scans.values()
            if s.repository_id == repository_id
            and s.status is ScanStatus.COMPLETED
            and repository.is_default_branch(s.ref)
        ]
        if not candidates:
            return frozenset()
        latest = max(candidates, key=lambda s: s.finished_at or s.queued_at)
        return frozenset(
            self._s.findings[fid].fingerprint
            for (sid, fid) in self._s.occurrences
            if sid == latest.id
        )

    def list(self, query: FindingQuery, page: PageRequest) -> Page[FindingView]:
        rows = [
            f
            for f in sorted(self._s.findings.values(), key=lambda f: str(f.id))
            if f.repository_id == query.repository_id
            and (not query.severities or f.severity in query.severities)
            and (not query.states or f.triage_state in query.states)
            and (not query.scanner_kinds or f.scanner_kind in query.scanner_kinds)
        ]
        views = []
        for f in rows:
            occurrence = (
                self._s.occurrences.get((query.seen_in_scan, f.id))
                if query.seen_in_scan is not None
                else None
            )
            if query.seen_in_scan is not None and occurrence is None:
                continue
            if query.only_new and (occurrence is None or not occurrence.is_new):
                continue
            views.append(FindingView(f, occurrence))
        offset = int(page.cursor) if page.cursor else 0
        chunk = views[offset : offset + page.limit]
        more = offset + page.limit < len(views)
        return Page(tuple(chunk), str(offset + page.limit) if more else None)

    def view_of(self, finding_id: FindingId) -> FindingView:
        finding = self.get(finding_id)
        return FindingView(
            finding, self._s.occurrences.get((finding.last_seen_scan_id, finding.id))
        )

    def decisions_of(self, finding_id: FindingId) -> Sequence[TriageDecision]:
        found = [d for d in self._s.decisions if d.finding_id == finding_id]
        return sorted(found, key=lambda d: (d.decided_at, d.id.value), reverse=True)[:200]

    def views_for_scan(
        self, scan_id: ScanId, *, only_new: bool, limit: int
    ) -> Sequence[FindingView]:
        views = [
            FindingView(self._s.findings[fid], occ)
            for (sid, fid), occ in self._s.occurrences.items()
            if sid == scan_id and (occ.is_new or not only_new)
        ]
        views.sort(key=lambda v: (-v.finding.severity.rank, str(v.finding.id)))
        return views[:limit]

    def open_counts_by_organization(
        self, organization_ids: Collection[OrganizationId]
    ) -> Mapping[OrganizationId, SeverityCounts]:
        wanted = set(organization_ids)
        grouped: dict[OrganizationId, list[Finding]] = {}
        for f in self._s.findings.values():
            org = self._s.repositories[f.repository_id].organization_id
            if org in wanted and f.triage_state is TriageState.OPEN:
                grouped.setdefault(org, []).append(f)
        return {k: SeverityCounts.of(f.severity for f in v) for k, v in grouped.items()}

    def open_counts_by_repository(
        self, organization_id: OrganizationId
    ) -> Mapping[RepositoryId, SeverityCounts]:
        grouped: dict[RepositoryId, list[Finding]] = {}
        for f in self._s.findings.values():
            repository = self._s.repositories[f.repository_id]
            if repository.organization_id == organization_id and f.triage_state is TriageState.OPEN:
                grouped.setdefault(f.repository_id, []).append(f)
        return {k: SeverityCounts.of(f.severity for f in v) for k, v in grouped.items()}


class InMemoryMemberships(_Repo):
    def list_for_user(self, user: UserId) -> Sequence[MembershipView]:
        views = [
            MembershipView(
                org,
                self._s.organizations[org].account_login,
                self._s.organizations[org].status,
                role,
            )
            for (member, org), role in self._s.memberships.items()
            if member == user
            and self._s.organizations[org].status is not OrganizationStatus.UNINSTALLED
        ]
        return sorted(views, key=lambda v: (v.account_login, v.organization_id.value))

    def list_members(self, organization_id: OrganizationId) -> Sequence[MemberView]:
        rank = {Role.ADMIN: 0, Role.TRIAGER: 1, Role.VIEWER: 2}
        members = []
        for (user, org), role in self._s.memberships.items():
            if org != organization_id:
                continue
            profile = self._s.users.get(user)
            members.append(
                MemberView(
                    user,
                    profile.display_name if profile else None,
                    profile.email if profile else None,
                    role,
                )
            )
        return sorted(members, key=lambda m: (rank[m.role], m.display_name or "", m.user_id.value))

    def role_of(self, user: UserId, organization_id: OrganizationId) -> Role | None:
        return self._s.memberships.get((user, organization_id))

    def set_role(
        self,
        user: UserId,
        organization_id: OrganizationId,
        role: Role,
        *,
        granted_by: UserId | None,
        at: datetime,
    ) -> None:
        self._s.memberships[(user, organization_id)] = role

    def remove(self, user: UserId, organization_id: OrganizationId) -> None:
        self._s.memberships.pop((user, organization_id), None)

    def count_admins(self, organization_id: OrganizationId) -> int:
        return sum(
            1
            for (_, org), role in self._s.memberships.items()
            if org == organization_id and role is Role.ADMIN
        )

    def lock_admins(self, organization_id: OrganizationId) -> int:
        self._s.admin_locks.append(organization_id)
        return self.count_admins(organization_id)


class InMemoryTenants(_Repo):
    def list_repositories(self, organization_id: OrganizationId) -> Sequence[Repository]:
        found = [r for r in self._s.repositories.values() if r.organization_id == organization_id]
        return sorted(found, key=lambda r: (r.ref.owner, r.ref.name))

    def get_repository(self, repository_id: RepositoryId) -> Repository:
        try:
            return self._s.repositories[repository_id]
        except KeyError:
            raise NotFoundError("repository") from None

    def find_repository_by_github_id(self, github_repo_id: int) -> Repository | None:
        return next(
            (r for r in self._s.repositories.values() if r.github_repo_id == github_repo_id), None
        )

    def get_organization(self, organization_id: OrganizationId) -> Organization:
        try:
            return self._s.organizations[organization_id]
        except KeyError:
            raise NotFoundError("organization") from None


class InMemoryTenantAdmin(_Repo):
    def find_organization_by_installation(self, installation_id: int) -> Organization | None:
        return next(
            (
                o
                for o in self._s.organizations.values()
                if o.github_installation_id == installation_id
            ),
            None,
        )

    def find_organization_by_account(self, account_id: int) -> Organization | None:
        return next(
            (o for o in self._s.organizations.values() if o.github_account_id == account_id), None
        )

    def find_organizations_by_login(self, login: str) -> Sequence[Organization]:
        return [o for o in self._s.organizations.values() if o.account_login == login]

    def add_organization(self, organization: Organization) -> None:
        self._s.organizations[organization.id] = organization

    def update_organization(
        self, organization: Organization, *, uninstalled_at: datetime | None
    ) -> None:
        if organization.id not in self._s.organizations:
            raise NotFoundError("organization")
        self._s.organizations[organization.id] = organization
        if uninstalled_at is None:
            self._s.uninstalled_at.pop(organization.id, None)
        else:
            self._s.uninstalled_at[organization.id] = uninstalled_at

    def list_repositories(self, organization_id: OrganizationId) -> Sequence[Repository]:
        return sorted(
            (r for r in self._s.repositories.values() if r.organization_id == organization_id),
            key=lambda r: r.github_repo_id,
        )

    def add_repository(self, repository: Repository) -> None:
        self._s.repositories[repository.id] = repository

    def update_repository(self, repository: Repository) -> None:
        if repository.id not in self._s.repositories:
            raise NotFoundError("repository")
        self._s.repositories[repository.id] = repository

    def list_installation_ids(self) -> Sequence[int]:
        return sorted(
            o.github_installation_id
            for o in self._s.organizations.values()
            if o.status is not OrganizationStatus.UNINSTALLED
        )


class InMemoryUsers(_Repo):
    def record_login(self, identity: OidcIdentity, *, new_id: UUID, at: datetime) -> UserId:
        existing = next(
            (
                u
                for u in self._s.users.values()
                if u.issuer == identity.issuer and u.subject == identity.subject
            ),
            None,
        )
        user_id = existing.id if existing else UserId(new_id)
        self._s.users[user_id] = UserSummary(
            id=user_id,
            issuer=identity.issuer,
            subject=identity.subject,
            display_name=identity.display_name,
            email=identity.email,
            last_login_at=at,
        )
        return user_id

    def get(self, user: UserId) -> UserSummary:
        try:
            return self._s.users[user]
        except KeyError:
            raise NotFoundError("user") from None

    def list_recent(self, *, limit: int) -> Sequence[UserSummary]:
        ordered = sorted(
            self._s.users.values(),
            key=lambda u: u.last_login_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return ordered[:limit]


class InMemorySessions(_Repo):
    def add(self, record: SessionRecord) -> None:
        if record.token_hash in self._s.sessions:
            raise ConcurrentModificationError("session already exists")
        self._s.sessions[record.token_hash] = record

    def get(self, token_hash: bytes) -> SessionRecord | None:
        return self._s.sessions.get(token_hash)

    def touch(self, token_hash: bytes, at: datetime) -> None:
        self._s.sessions[token_hash] = replace(self._s.sessions[token_hash], last_seen_at=at)

    def delete(self, token_hash: bytes) -> bool:
        return self._s.sessions.pop(token_hash, None) is not None

    def delete_expired(self, now: datetime, *, limit: int) -> int:
        expired = [h for h, r in self._s.sessions.items() if r.expires_at <= now][:limit]
        for token_hash in expired:
            del self._s.sessions[token_hash]
        return len(expired)

    def delete_for_user(self, user: UserId) -> int:
        mine = [h for h, r in self._s.sessions.items() if r.user_id == user]
        for token_hash in mine:
            del self._s.sessions[token_hash]
        return len(mine)

    def list_for_user(self, user: UserId, *, limit: int) -> Sequence[SessionRecord]:
        mine = [r for r in self._s.sessions.values() if r.user_id == user]
        mine.sort(key=lambda r: (-r.last_seen_at.timestamp(), r.token_hash))
        return mine[:limit]


class InMemoryLoginFlows(_Repo):
    def add(self, flow: LoginFlow) -> None:
        self._s.login_flows[flow.state_hash] = flow

    def consume(self, state_hash: bytes, now: datetime) -> LoginFlow | None:
        flow = self._s.login_flows.pop(state_hash, None)
        return flow if flow is not None and flow.expires_at > now else None

    def count_live(self, now: datetime) -> int:
        return sum(1 for f in self._s.login_flows.values() if f.expires_at > now)

    def delete_expired(self, now: datetime, *, limit: int) -> int:
        expired = [h for h, f in self._s.login_flows.items() if f.expires_at <= now][:limit]
        for state_hash in expired:
            del self._s.login_flows[state_hash]
        return len(expired)


class InMemoryScanLogs(_Repo):
    def append(self, scan_id: ScanId, level: LogLevel, message: str, at: datetime) -> None:
        entries = self._s.logs.setdefault(scan_id, [])
        entries.append(ScanLogEntry(seq=len(entries) + 1, at=at, level=level, message=message))

    def list(self, scan_id: ScanId, *, after_seq: int, limit: int) -> Sequence[ScanLogEntry]:
        return [e for e in self._s.logs.get(scan_id, []) if e.seq > after_seq][:limit]


class InMemoryDeliveries(_Repo):
    def exists(self, delivery_id: str) -> bool:
        return delivery_id in self._s.deliveries

    def add(self, delivery_id: str, event: str, received_at: datetime, outcome: str) -> None:
        if delivery_id in self._s.deliveries:
            raise ConcurrentModificationError("delivery already recorded")
        self._s.deliveries[delivery_id] = outcome
        self._s.delivery_log.append(DeliveryRecord(delivery_id, event, outcome, received_at))


class InMemoryPolicies(_Repo):
    def active_policy(self, organization_id: OrganizationId) -> PolicyRecord | None:
        return self._s.policies.get(organization_id)

    def publish(
        self,
        organization_id: OrganizationId,
        policy: QualityGatePolicy,
        *,
        policy_id: UUID,
        at: datetime,
    ) -> int:
        current = self._s.policies.get(organization_id)
        version = current.version + 1 if current else 1
        self._s.policies[organization_id] = PolicyRecord(str(policy_id), version, policy)
        return version

    def record_evaluation(
        self, scan_id: ScanId, policy_id: str | None, verdict: PolicyVerdict
    ) -> None:
        self._s.evaluations[scan_id] = verdict

    def get_evaluation(self, scan_id: ScanId) -> PolicyVerdict | None:
        return self._s.evaluations.get(scan_id)


class InMemoryAudit(_Repo):
    def append(self, entry: AuditEntry) -> None:
        self._s.audit.append(entry)


class InMemoryInsights(_Repo):
    def _label(self, repository_id: RepositoryId) -> RepositoryLabel:
        repo = self._s.repositories[repository_id]
        return RepositoryLabel(repo.id, repo.ref.owner, repo.ref.name)

    def _findings(self, organization_id: OrganizationId) -> list[Finding]:
        return sorted(
            (
                f
                for f in self._s.findings.values()
                if self._s.repositories[f.repository_id].organization_id == organization_id
            ),
            key=lambda f: str(f.id),
        )

    def open_buckets(self, organization_id: OrganizationId) -> Sequence[OpenBucket]:
        tally: dict[tuple[RepositoryId, str, Severity], int] = {}
        for f in self._findings(organization_id):
            if f.triage_state is TriageState.OPEN:
                key = (f.repository_id, f.scanner_kind.value, f.severity)
                tally[key] = tally.get(key, 0) + 1
        return [OpenBucket(self._label(r), s, sev, n) for (r, s, sev), n in tally.items()]

    def rule_stats(self, organization_id: OrganizationId, *, limit: int) -> Sequence[RuleStat]:
        groups: dict[tuple[str, str, str], list[Finding]] = {}
        for f in self._findings(organization_id):
            key = (f.rule_id.value, f.scanner_kind.value, f.category.value)
            groups.setdefault(key, []).append(f)
        stats = [
            RuleStat(
                rule_id=rule,
                scanner=scanner,
                category=category,
                max_severity=max(f.severity for f in items),
                open=sum(f.triage_state is TriageState.OPEN for f in items),
                total=len(items),
                repositories=len({f.repository_id for f in items}),
                last_seen_at=max(f.last_seen_at for f in items),
            )
            for (rule, scanner, category), items in groups.items()
        ]
        stats.sort(key=lambda s: (-s.open, -s.max_severity.rank, s.rule_id))
        return stats[:limit]

    def fix_times(self, organization_id: OrganizationId) -> Sequence[FixTime]:
        groups: dict[Severity, list[float]] = {}
        for f in self._findings(organization_id):
            if f.triage_state is TriageState.FIXED:
                seconds = (f.last_seen_at - f.first_seen_at).total_seconds()
                groups.setdefault(f.severity, []).append(seconds)
        return [
            FixTime(s, len(v), sum(v) / len(v)) for s, v in sorted(groups.items(), reverse=True)
        ]

    def overdue_counts(
        self, organization_id: OrganizationId, cutoffs: Mapping[Severity, datetime]
    ) -> Mapping[Severity, int]:
        found: dict[Severity, int] = {}
        for f in self._findings(organization_id):
            cutoff = cutoffs.get(f.severity)
            if (
                f.triage_state is TriageState.OPEN
                and cutoff is not None
                and f.first_seen_at < cutoff
            ):
                found[f.severity] = found.get(f.severity, 0) + 1
        return found

    def open_summaries(
        self, organization_id: OrganizationId, *, since: datetime
    ) -> Sequence[SummaryPoint]:
        points = [
            SummaryPoint(s.repository_id, s.finished_at, s.summary.open)
            for s in self._s.scans.values()
            if s.summary is not None
            and s.finished_at is not None
            and s.status is ScanStatus.COMPLETED
            and self._s.repositories[s.repository_id].organization_id == organization_id
            and self._s.repositories[s.repository_id].is_default_branch(s.ref)
        ]
        return sorted(points, key=lambda p: p.finished_at)

    def recent_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        scans = [
            s
            for s in self._s.scans.values()
            if self._s.repositories[s.repository_id].organization_id == organization_id
        ]
        scans.sort(key=lambda s: (s.is_terminal, -s.queued_at.timestamp()))
        return [ScanActivity(s, self._label(s.repository_id)) for s in scans[:limit]]

    def accepted_risks(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[FindingRow]:
        risks = [
            f
            for f in self._findings(organization_id)
            if f.triage_state is TriageState.ACCEPTED_RISK
        ]
        risks.sort(key=lambda f: (f.triage_expires_at or f.last_seen_at, str(f.id)))
        rows = []
        for f in risks[:limit]:
            decisions = [
                d
                for d in self._s.decisions
                if d.finding_id == f.id and d.state is TriageState.ACCEPTED_RISK
            ]
            latest = max(decisions, key=lambda d: d.decided_at, default=None)
            text = latest.justification.value if latest and latest.justification else None
            rows.append(FindingRow(f, self._label(f.repository_id), text))
        return rows

    def dependency_findings(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[FindingRow]:
        found = [
            f for f in self._findings(organization_id) if f.category is FindingCategory.DEPENDENCY
        ]
        found.sort(key=lambda f: (f.triage_state is not TriageState.OPEN, -f.severity.rank))
        return [FindingRow(f, self._label(f.repository_id)) for f in found[:limit]]

    def policy_versions(self, organization_id: OrganizationId) -> Sequence[PolicyVersion]:
        record = self._s.policies.get(organization_id)
        if record is None:
            return []
        return [
            PolicyVersion(record.version, True, datetime(2026, 1, 1, tzinfo=UTC), record.policy)
        ]

    def audit_entries(
        self, organization_id: OrganizationId, *, limit: int, before: int | None
    ) -> Sequence[AuditRecord]:
        records = []
        for index, entry in enumerate(self._s.audit, start=1):
            if entry.organization_id != organization_id or (before is not None and index >= before):
                continue
            profile = self._s.users.get(entry.actor) if entry.actor else None
            records.append(
                AuditRecord(
                    id=index,
                    occurred_at=entry.occurred_at,
                    action=entry.action,
                    actor=entry.actor,
                    actor_name=profile.display_name if profile else None,
                    target_type=entry.target_type,
                    target_id=entry.target_id,
                    details=dict(entry.details),
                )
            )
        return list(reversed(records))[:limit]

    def _org_scans(self, organization_id: OrganizationId) -> list[Scan]:
        return [
            s
            for s in self._s.scans.values()
            if self._s.repositories[s.repository_id].organization_id == organization_id
        ]

    def _located(self, f: Finding, justification: str | None = None) -> FindingRow:
        occurrence = self._s.occurrences.get((f.last_seen_scan_id, f.id))
        return FindingRow(f, self._label(f.repository_id), justification, occurrence)

    def pull_request_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        latest: dict[tuple[RepositoryId, int], Scan] = {}
        for s in self._org_scans(organization_id):
            if s.pull_request_number is None:
                continue
            key = (s.repository_id, s.pull_request_number)
            if key not in latest or s.queued_at > latest[key].queued_at:
                latest[key] = s
        found = sorted(latest.values(), key=lambda s: s.queued_at, reverse=True)
        return [ScanActivity(s, self._label(s.repository_id)) for s in found[:limit]]

    @staticmethod
    def _matches(f: Finding, filters: FindingFilters, *, states: bool = True) -> bool:
        return (
            (not filters.severities or f.severity in filters.severities)
            and (not states or not filters.states or f.triage_state in filters.states)
            and (not filters.scanner_kinds or f.scanner_kind in filters.scanner_kinds)
            and (filters.repository_id is None or f.repository_id == filters.repository_id)
        )

    @staticmethod
    def _queue_key(f: Finding) -> tuple[int, datetime, str]:
        return (-f.severity.rank, f.first_seen_at, f.id.value.hex)

    def triage_queue(
        self,
        organization_id: OrganizationId,
        filters: FindingFilters,
        *,
        after: QueueCursor | None,
        limit: int,
    ) -> Sequence[FindingRow]:
        found = [
            f
            for f in self._findings(organization_id)
            if f.triage_state is TriageState.OPEN and self._matches(f, filters, states=False)
        ]
        found.sort(key=self._queue_key)
        if after is not None:
            mark = (-after.rank, after.first_seen_at, after.finding_id.value.hex)
            found = [f for f in found if self._queue_key(f) > mark]
        return [self._located(f) for f in found[:limit]]

    def failed_scans(self, organization_id: OrganizationId, *, limit: int) -> Sequence[FailedScan]:
        scans = self._org_scans(organization_id)
        failed = [s for s in scans if s.status in {ScanStatus.FAILED, ScanStatus.TIMED_OUT}]
        failed.sort(key=lambda s: s.queued_at, reverse=True)
        return [
            FailedScan(
                s,
                self._label(s.repository_id),
                any(
                    o.repository_id == s.repository_id
                    and o.ref == s.ref
                    and o.status is ScanStatus.COMPLETED
                    and o.queued_at > s.queued_at
                    for o in scans
                ),
            )
            for s in failed[:limit]
        ]

    def completed_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        done = [
            s
            for s in self._org_scans(organization_id)
            if s.status is ScanStatus.COMPLETED and s.summary is not None and s.finished_at
        ]
        done.sort(key=lambda s: s.finished_at or s.queued_at, reverse=True)
        return [ScanActivity(s, self._label(s.repository_id)) for s in done[:limit]]

    def export_rows(
        self,
        organization_id: OrganizationId,
        view: ExportView,
        filters: FindingFilters,
        *,
        limit: int,
    ) -> Sequence[FindingRow]:
        if view is ExportView.TRIAGE:
            return self.triage_queue(organization_id, filters, after=None, limit=limit)
        if view is ExportView.ACCEPTED_RISKS:
            risks = self.accepted_risks(organization_id, limit=limit)
            return [
                self._located(r.finding, r.justification)
                for r in risks
                if self._matches(r.finding, filters, states=False)
            ]
        if view is ExportView.DEPENDENCIES:
            deps = self.dependency_findings(organization_id, limit=limit)
            return [self._located(r.finding) for r in deps if self._matches(r.finding, filters)]
        if filters.repository_id is None:
            return []
        found = [f for f in self._findings(organization_id) if self._matches(f, filters)]
        found.sort(key=lambda f: (-f.severity.rank, str(f.id)))
        return [self._located(f) for f in found[:limit]]


class InMemoryPlatformAdmins(_Repo):
    def is_admin(self, user: UserId) -> bool:
        return user in self._s.platform_admins

    def list_admins(self) -> Sequence[UserId]:
        return list(self._s.platform_admins)

    def grant(self, user: UserId, at: datetime) -> bool:
        if user in self._s.platform_admins:
            return False
        self._s.platform_admins[user] = at
        return True

    def revoke(self, user: UserId) -> bool:
        return self._s.platform_admins.pop(user, None) is not None


class InMemoryPlatformQueries(_Repo):
    def users(self, *, limit: int, now: datetime) -> Sequence[PlatformUser]:
        found = []
        for user in self._s.users.values():
            memberships = tuple(
                UserMembership(org, self._s.organizations[org].account_login, role)
                for (uid, org), role in sorted(
                    self._s.memberships.items(), key=lambda i: str(i[0][1])
                )
                if uid == user.id and org in self._s.organizations
            )
            live = sum(
                1 for r in self._s.sessions.values() if r.user_id == user.id and r.expires_at > now
            )
            found.append(
                PlatformUser(
                    id=user.id,
                    display_name=user.display_name,
                    email=user.email,
                    last_login_at=user.last_login_at,
                    live_sessions=live,
                    platform_admin=user.id in self._s.platform_admins,
                    memberships=memberships,
                )
            )
        return found[:limit]

    def organizations(self) -> Sequence[PlatformOrganization]:
        found = []
        for org in self._s.organizations.values():
            roles = [r for (_, o), r in self._s.memberships.items() if o == org.id]
            found.append(
                PlatformOrganization(
                    org.id, org.account_login, org.status, len(roles), roles.count(Role.ADMIN)
                )
            )
        return sorted(found, key=lambda o: o.login)

    def recent_deliveries(self, *, limit: int) -> Sequence[DeliveryRecord]:
        return sorted(self._s.delivery_log, key=lambda d: d.received_at, reverse=True)[:limit]

    def delivery_outcomes(self, *, since: datetime) -> Mapping[str, int]:
        found: dict[str, int] = {}
        for record in self._s.delivery_log:
            if record.received_at >= since:
                found[record.outcome] = found.get(record.outcome, 0) + 1
        return found

    def webhook_counters(self, *, since: datetime) -> Sequence[CounterBucket]:
        return [
            CounterBucket(minute, reason, n)
            for (minute, reason), n in sorted(
                self._s.webhook_counters.items(), key=lambda i: (i[0][0], i[0][1].value)
            )
            if minute >= since
        ]

    def health_reports(self) -> Sequence[ComponentHealth]:
        return list(self._s.health.values())


class InMemoryHealthStore(_Repo):
    def save(self, reports: Sequence[ComponentHealth]) -> None:
        for report in reports:
            self._s.health[report.component] = report


class InMemoryWebhookCounters:
    def __init__(self, store: Store) -> None:
        self._store = store
        self.flushes = 0

    def add(self, counts: Mapping[tuple[datetime, WebhookReason], int]) -> None:
        self.flushes += 1
        for key, n in counts.items():
            current = self._store.state.webhook_counters.get(key, 0)
            self._store.state.webhook_counters[key] = current + n


class _Outbox:
    def __init__(self) -> None:
        self.pending: list[DomainEvent] = []

    def publish(self, events: Sequence[DomainEvent]) -> None:
        self.pending.extend(events)


class InMemoryUnitOfWork:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._committed = False

    def __enter__(self) -> Self:
        self._working = self._store.state.clone()
        self._outbox = _Outbox()
        self.scans = InMemoryScans(self._working)
        self.findings = InMemoryFindings(self._working)
        self.memberships = InMemoryMemberships(self._working)
        self.users = InMemoryUsers(self._working)
        self.sessions = InMemorySessions(self._working)
        self.login_flows = InMemoryLoginFlows(self._working)
        self.tenants = InMemoryTenants(self._working)
        self.tenant_admin = InMemoryTenantAdmin(self._working)
        self.scan_logs = InMemoryScanLogs(self._working)
        self.webhook_deliveries = InMemoryDeliveries(self._working)
        self.policies = InMemoryPolicies(self._working)
        self.audit = InMemoryAudit(self._working)
        self.insights = InMemoryInsights(self._working)
        self.platform_admins = InMemoryPlatformAdmins(self._working)
        self.platform = InMemoryPlatformQueries(self._working)
        self.health = InMemoryHealthStore(self._working)
        self.events = self._outbox
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del self._working

    def commit(self) -> None:
        self._working.outbox.extend(self._outbox.pending)
        self._outbox.pending.clear()
        self._store.state = self._working.clone()
        self._store.commits += 1


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 10, 11, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


class SequentialIds:
    def __init__(self) -> None:
        self._n = 0

    def new_uuid(self) -> UUID:
        self._n += 1
        return UUID(int=(4 << 76) | (0x8 << 60) | self._n)


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[ScanStep, ScanId]] = []
        self.revoked: list[ScanId] = []

    def enqueue(self, step: ScanStep, scan_id: ScanId) -> None:
        self.enqueued.append((step, scan_id))

    def revoke(self, scan_id: ScanId) -> None:
        self.revoked.append(scan_id)


class FakeFetcher:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.checkouts: list[tuple[int, RepoRef, CommitSha]] = []
        self.released = 0

    @contextmanager
    def _workspace(self) -> Iterator[WorkspaceHandle]:
        try:
            yield WorkspaceHandle(f"ws-{len(self.checkouts)}")
        finally:
            self.released += 1

    def checkout(
        self, *, installation_id: int, repo: RepoRef, commit: CommitSha, limits: FetchLimits
    ) -> AbstractContextManager[WorkspaceHandle]:
        if self.error is not None:
            raise self.error
        self.checkouts.append((installation_id, repo, commit))
        return self._workspace()


class FakeScanner:
    def __init__(self, kind: str, output: ScannerOutput | Exception) -> None:
        self._kind = ScannerKind(kind)
        self.output = output
        self.calls: list[WorkspaceHandle] = []

    @property
    def kind(self) -> ScannerKind:
        return self._kind

    def scan(self, workspace: WorkspaceHandle, limits: ScanLimits) -> ScannerOutput:
        self.calls.append(workspace)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class FakeRegistry:
    def __init__(self, *scanners: FakeScanner) -> None:
        self._scanners = scanners

    def scanners(self) -> Sequence[FakeScanner]:
        return self._scanners


class DomainPolicyEvaluator:
    def evaluate(
        self, policy: QualityGatePolicy, findings: Sequence[FindingSnapshot], now: datetime
    ) -> PolicyVerdict:
        return evaluate_quality_gates(policy, findings, now)


class RecordingVcs:
    def __init__(self) -> None:
        self.checks: list[tuple[VcsTarget, Scan]] = []
        self.sarif: list[tuple[VcsTarget, ScanId, int]] = []
        self.comments: list[tuple[VcsTarget, ScanId, PrCommentBody]] = []

    def publish_check(self, target: VcsTarget, scan: Scan) -> None:
        self.checks.append((target, scan))

    def upload_sarif(self, target: VcsTarget, scan: Scan, findings: Sequence[FindingView]) -> None:
        self.sarif.append((target, scan.id, len(findings)))

    def upsert_pr_comment(self, target: VcsTarget, scan: Scan, body: PrCommentBody) -> None:
        self.comments.append((target, scan.id, body))
