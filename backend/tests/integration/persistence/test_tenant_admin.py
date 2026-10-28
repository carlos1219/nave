from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from nave.application.dto.installations import SyncOutcome
from nave.application.errors import ConcurrentModificationError, NotFoundError
from nave.application.use_cases.sync_installation import SyncInstallation, SyncInstallationCommand
from nave.domain.entities.tenancy import Organization, OrganizationStatus, Repository
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import OrganizationId, RepositoryId
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from nave.infrastructure.system import RandomIdGenerator
from tests.fakes.installations import FakeDirectory
from tests.fakes.memory import FakeClock
from tests.integration.persistence.seed import ORG, REPO, at, seed_tenancy
from tests.unit.application.test_sync_installation import info, repo


@pytest.fixture
def uow(engine: Engine) -> SqlUnitOfWorkFactory:
    return SqlUnitOfWorkFactory(engine)


def org(n: int, installation: int, account: int | None) -> Organization:
    return Organization(
        id=OrganizationId(UUID(int=0x100 + n)),
        github_installation_id=installation,
        account_login=f"org{n}",
        status=OrganizationStatus.ACTIVE,
        github_account_id=account,
    )


class TestTenantAdmin:
    def test_organization_lifecycle(self, uow: SqlUnitOfWorkFactory) -> None:
        created = org(1, 7, 70)
        with uow() as tx:
            tx.tenant_admin.add_organization(created)
            tx.commit()
        with uow() as tx:
            assert tx.tenant_admin.find_organization_by_installation(7) == created
            assert tx.tenant_admin.find_organization_by_account(70) == created
            assert tx.tenant_admin.find_organization_by_account(71) is None
            gone = replace(created, status=OrganizationStatus.UNINSTALLED)
            tx.tenant_admin.update_organization(gone, uninstalled_at=at(5))
            tx.commit()
        with uow() as tx:
            assert tx.tenant_admin.list_installation_ids() == []
            back = replace(created, github_installation_id=8)
            tx.tenant_admin.update_organization(back, uninstalled_at=None)
            assert tx.tenant_admin.list_installation_ids() == [8]
            tx.commit()

    def test_uninstalled_status_requires_a_timestamp_and_vice_versa(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        created = org(1, 7, 70)
        with uow() as tx:
            tx.tenant_admin.add_organization(created)
            tx.commit()
        with pytest.raises(DBAPIError), uow() as tx:
            tx.tenant_admin.update_organization(
                replace(created, status=OrganizationStatus.UNINSTALLED), uninstalled_at=None
            )
            tx.commit()
        with pytest.raises(DBAPIError), uow() as tx:
            tx.tenant_admin.update_organization(created, uninstalled_at=at(1))
            tx.commit()

    def test_an_account_cannot_back_two_organizations(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            tx.tenant_admin.add_organization(org(1, 7, 70))
            tx.commit()
        with pytest.raises(ConcurrentModificationError), uow() as tx:
            tx.tenant_admin.add_organization(org(2, 8, 70))
            tx.commit()

    def test_unknown_rows_are_not_found(self, uow: SqlUnitOfWorkFactory) -> None:
        with pytest.raises(NotFoundError), uow() as tx:
            tx.tenant_admin.update_organization(org(9, 99, None), uninstalled_at=None)
        with pytest.raises(NotFoundError), uow() as tx:
            tx.tenant_admin.update_repository(
                Repository(
                    id=RepositoryId(UUID(int=0x999)),
                    organization_id=ORG,
                    github_repo_id=1,
                    ref=RepoRef("acme", "x"),
                    default_branch=GitRef("main"),
                    enabled=True,
                )
            )

    def test_repository_update_cannot_cross_tenants(
        self, engine: Engine, uow: SqlUnitOfWorkFactory
    ) -> None:
        seed_tenancy(engine)
        with uow() as tx:
            tx.tenant_admin.add_organization(org(1, 7, 70))
            tx.commit()
        with uow() as tx:
            current = tx.tenants.get_repository(REPO)
        foreign = replace(current, organization_id=OrganizationId(UUID(int=0x101)), enabled=False)
        with pytest.raises(NotFoundError), uow() as tx:
            tx.tenant_admin.update_repository(foreign)
            tx.commit()
        with uow() as tx:
            assert tx.tenants.get_repository(REPO) == current


def test_app_role_can_update_tenancy_but_not_delete_it(engine: Engine) -> None:
    seed_tenancy(engine)
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE nave_app"))
        conn.execute(text("UPDATE organizations SET account_login = 'renamed'"))
        conn.execute(text("UPDATE repositories SET enabled = false"))
    with pytest.raises(DBAPIError, match="permission denied"), engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE nave_app"))
        conn.execute(text("DELETE FROM organizations"))
    with pytest.raises(DBAPIError, match="permission denied"), engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE nave_maintenance"))
        conn.execute(text("UPDATE organizations SET status = 'ACTIVE'"))


def test_sync_installation_on_postgres(uow: SqlUnitOfWorkFactory) -> None:
    directory = FakeDirectory()
    directory.installations[10] = info()
    directory.repositories[10] = [repo(1, "api"), repo(2, "web")]
    use_case = SyncInstallation(
        uow_factory=uow,
        directory=directory,
        clock=FakeClock(),
        ids=RandomIdGenerator(),
        allowed_accounts=("acme",),
    )
    assert use_case.execute(SyncInstallationCommand(10)) is SyncOutcome.REGISTERED
    directory.repositories[10] = [repo(1, "api-renamed")]
    assert use_case.execute(SyncInstallationCommand(10)) is SyncOutcome.UPDATED
    del directory.installations[10]
    assert use_case.execute(SyncInstallationCommand(10)) is SyncOutcome.UNINSTALLED
    with uow() as tx:
        stored = tx.tenant_admin.find_organization_by_installation(10)
        assert stored is not None
        assert stored.status is OrganizationStatus.UNINSTALLED
        repos = tx.tenant_admin.list_repositories(stored.id)
    assert [(r.github_repo_id, r.ref.name, r.enabled) for r in repos] == [
        (1, "api-renamed", True),
        (2, "web", False),
    ]
