"""Consultas de la API REST sobre PostgreSQL real (ADR 0017)."""

from uuid import UUID

import pytest
from sqlalchemy import Engine

from nave.application.dto.views import MemberView, PageRequest
from nave.application.errors import NotFoundError
from nave.application.services.authorization import Authorizer
from nave.application.use_cases.browse import ListScans, ListScansQuery
from nave.application.use_cases.manage_membership import (
    ManageMembership,
    ManageMembershipCommand,
)
from nave.domain.entities.tenancy import OrganizationStatus
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.value_objects.finding_text import FindingMessage, Justification
from nave.domain.value_objects.identifiers import (
    FindingId,
    OrganizationId,
    TriageDecisionId,
    UserId,
)
from nave.domain.value_objects.location import SourceLocation
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.source_path import SourcePath
from nave.domain.value_objects.triage_state import TriageState
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.fakes.memory import FakeClock
from tests.integration.persistence.seed import (
    ADMIN,
    ORG,
    OTHER_REPO,
    REPO,
    TRIAGER,
    at,
    new_finding,
    new_scan,
    scan_id,
    seed_tenancy,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def uow(engine: Engine) -> SqlUnitOfWorkFactory:
    seed_tenancy(engine)
    return SqlUnitOfWorkFactory(engine)


class TestScanListing:
    def test_newest_first_with_a_strict_keyset(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            for n in range(1, 6):
                tx.scans.add(new_scan(n, minute=n))
            tx.scans.add(new_scan(9, repository=OTHER_REPO, minute=99))
            tx.commit()
        with uow() as tx:
            first = tx.scans.list_for_repository(REPO, limit=2, before=None)
            assert [s.id for s in first] == [scan_id(5), scan_id(4)]
            last = first[-1]
            second = tx.scans.list_for_repository(REPO, limit=10, before=(last.queued_at, last.id))
            assert [s.id for s in second] == [scan_id(3), scan_id(2), scan_id(1)]

    def test_equal_timestamps_are_ordered_by_id_without_gaps_or_repeats(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        with uow() as tx:
            for n in range(1, 5):
                tx.scans.add(new_scan(n, minute=7))
            tx.commit()
        seen = []
        before = None
        with uow() as tx:
            while True:
                page = tx.scans.list_for_repository(REPO, limit=1, before=before)
                if not page:
                    break
                seen.append(page[0].id)
                before = (page[0].queued_at, page[0].id)
        assert seen == [scan_id(4), scan_id(3), scan_id(2), scan_id(1)]

    def test_use_case_pages_and_authorizes(self, uow: SqlUnitOfWorkFactory, engine: Engine) -> None:
        with uow() as tx:
            for n in range(1, 4):
                tx.scans.add(new_scan(n, minute=n))
            tx.memberships.set_role(TRIAGER, ORG, Role.VIEWER, granted_by=ADMIN, at=at(0))
            tx.commit()
        use_case = ListScans(
            uow_factory=uow, authorizer=Authorizer(uow_factory=uow, clock=FakeClock(at(0)))
        )
        page = use_case.execute(
            ListScansQuery(actor=TRIAGER, repository_id=REPO, page=PageRequest(limit=2))
        )
        assert [s.id for s in page.items] == [scan_id(3), scan_id(2)]
        assert page.next_cursor
        rest = use_case.execute(
            ListScansQuery(
                actor=TRIAGER,
                repository_id=REPO,
                page=PageRequest(limit=2, cursor=page.next_cursor),
            )
        )
        assert [s.id for s in rest.items] == [scan_id(1)]
        assert rest.next_cursor is None
        outsider = UserId(UUID(int=0xBEEF))
        with pytest.raises(NotFoundError):
            use_case.execute(ListScansQuery(actor=outsider, repository_id=REPO, page=PageRequest()))


class TestFindingDetail:
    def seed(self, uow: SqlUnitOfWorkFactory) -> FindingId:
        scan = new_scan(1)
        finding = new_finding(1, scan=scan.id)
        occurrence = FindingOccurrence(
            scan_id=scan.id,
            finding_id=finding.id,
            location=SourceLocation(SourcePath("app/x.py"), 3, 4),
            message=FindingMessage("bad"),
            snippet=None,
            is_new=True,
        )
        with uow() as tx:
            tx.scans.add(scan)
            tx.findings.add(finding)
            tx.findings.record_occurrences([occurrence])
            tx.commit()
        return finding.id

    def test_view_of_joins_the_last_seen_occurrence(self, uow: SqlUnitOfWorkFactory) -> None:
        finding_id = self.seed(uow)
        with uow() as tx:
            view = tx.findings.view_of(finding_id)
        assert view.finding.id == finding_id
        assert view.occurrence is not None
        assert view.occurrence.location is not None
        assert view.occurrence.location.start_line == 3

    def test_view_of_without_occurrence_and_unknown(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        scan = new_scan(1)
        finding = new_finding(2, scan=scan.id)
        with uow() as tx:
            tx.scans.add(scan)
            tx.findings.add(finding)
            tx.commit()
        with uow() as tx:
            assert tx.findings.view_of(finding.id).occurrence is None
            with pytest.raises(NotFoundError):
                tx.findings.view_of(FindingId(UUID(int=0xDEAD)))

    def test_decisions_newest_first(self, uow: SqlUnitOfWorkFactory) -> None:
        finding_id = self.seed(uow)
        with uow() as tx:
            for n, state in ((1, TriageState.FALSE_POSITIVE), (2, TriageState.OPEN)):
                tx.findings.add_decision(
                    TriageDecision(
                        id=TriageDecisionId(UUID(int=0xD00 + n)),
                        finding_id=finding_id,
                        state=state,
                        decided_at=at(n),
                        decided_by=TRIAGER,
                        justification=(
                            Justification("A long enough reason.")
                            if state is TriageState.FALSE_POSITIVE
                            else None
                        ),
                    )
                )
            tx.commit()
        with uow() as tx:
            decisions = tx.findings.decisions_of(finding_id)
        assert [d.state for d in decisions] == [TriageState.OPEN, TriageState.FALSE_POSITIVE]
        assert decisions[1].justification is not None
        assert decisions[1].decided_by == TRIAGER


class TestOpenFindingCounts:
    def seed(self, uow: SqlUnitOfWorkFactory, engine: Engine) -> None:
        scan = new_scan(1)
        with uow() as tx:
            tx.scans.add(scan)
            tx.findings.add(new_finding(1, scan=scan.id, severity=Severity.HIGH))
            tx.findings.add(new_finding(2, scan=scan.id, severity=Severity.CRITICAL))
            tx.findings.add(
                new_finding(3, scan=scan.id, severity=Severity.HIGH, repository=OTHER_REPO)
            )
            tx.findings.add(new_finding(4, scan=scan.id, severity=Severity.LOW))
            tx.commit()
        with engine.begin() as conn:
            conn.execute(
                t.findings.update()
                .where(t.findings.c.id == UUID(int=0xF004))
                .values(triage_state=TriageState.FIXED.value)
            )

    def test_only_open_findings_count_per_organization(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        self.seed(uow, engine)
        stranger = OrganizationId(UUID(int=0xFF))
        with uow() as tx:
            counts = tx.findings.open_counts_by_organization([ORG, stranger])
            assert tx.findings.open_counts_by_organization([]) == {}
        assert counts == {ORG: SeverityCounts(critical=1, high=2)}

    def test_per_repository_of_one_organization(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        self.seed(uow, engine)
        with uow() as tx:
            counts = tx.findings.open_counts_by_repository(ORG)
            assert tx.findings.open_counts_by_repository(OrganizationId(UUID(int=0xFF))) == {}
        assert counts == {
            REPO: SeverityCounts(critical=1, high=1),
            OTHER_REPO: SeverityCounts(high=1),
        }


class TestOrganizationQueries:
    def test_repositories_of_an_organization_sorted(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            repositories = tx.tenants.list_repositories(ORG)
        assert [r.ref.name for r in repositories] == ["app", "lib"]

    def test_memberships_of_a_user_exclude_uninstalled_organizations(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        with uow() as tx:
            tx.memberships.set_role(TRIAGER, ORG, Role.TRIAGER, granted_by=ADMIN, at=at(0))
            tx.commit()
        with uow() as tx:
            (view,) = tx.memberships.list_for_user(TRIAGER)
        assert (view.organization_id, view.account_login, view.role) == (ORG, "acme", Role.TRIAGER)
        assert view.status is OrganizationStatus.ACTIVE
        with engine.begin() as conn:
            conn.execute(
                t.organizations.update()
                .where(t.organizations.c.id == ORG.value)
                .values(status="UNINSTALLED", uninstalled_at=at(0))
            )
        with uow() as tx:
            assert tx.memberships.list_for_user(TRIAGER) == []

    def test_members_with_profiles_admins_first(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        with engine.begin() as conn:
            conn.execute(
                t.users.update()
                .where(t.users.c.id == TRIAGER.value)
                .values(display_name="Tris", email="t@example.com")
            )
        with uow() as tx:
            tx.memberships.set_role(TRIAGER, ORG, Role.VIEWER, granted_by=ADMIN, at=at(0))
            tx.memberships.set_role(ADMIN, ORG, Role.ADMIN, granted_by=None, at=at(0))
            tx.commit()
        with uow() as tx:
            members = tx.memberships.list_members(ORG)
        assert members == [
            MemberView(ADMIN, None, None, Role.ADMIN),
            MemberView(TRIAGER, "Tris", "t@example.com", Role.VIEWER),
        ]

    def test_managing_membership_of_an_unknown_user_is_not_found_not_a_database_error(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        with uow() as tx:
            tx.memberships.set_role(ADMIN, ORG, Role.ADMIN, granted_by=None, at=at(0))
            tx.commit()
        clock = FakeClock(at(0))
        use_case = ManageMembership(
            uow_factory=uow, authorizer=Authorizer(uow_factory=uow, clock=clock), clock=clock
        )
        with pytest.raises(NotFoundError):
            use_case.execute(
                ManageMembershipCommand(
                    actor=ADMIN, organization_id=ORG, user=UserId(UUID(int=0xBAD)), role=Role.VIEWER
                )
            )
