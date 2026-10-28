from collections.abc import Sequence
from dataclasses import replace

import pytest

from nave.application.dto.installations import InstallationInfo, RemoteRepository, SyncOutcome
from nave.application.errors import TransientError
from nave.application.use_cases.reconcile_installations import ReconcileInstallations
from nave.application.use_cases.sync_installation import SyncInstallation, SyncInstallationCommand
from nave.domain.entities.tenancy import OrganizationStatus
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.repo_ref import RepoRef
from tests.fakes.installations import FakeDirectory, FakeSyncQueue
from tests.fakes.memory import FakeClock, SequentialIds, Store


def info(
    installation_id: int = 10,
    *,
    account_id: int = 500,
    login: str = "Acme",
    suspended: bool = False,
) -> InstallationInfo:
    return InstallationInfo(
        installation_id=installation_id,
        account_id=account_id,
        account_login=login,
        suspended=suspended,
    )


def repo(repo_id: int, name: str, branch: str = "main", owner: str = "Acme") -> RemoteRepository:
    return RemoteRepository(
        github_repo_id=repo_id, ref=RepoRef(owner, name), default_branch=GitRef(branch)
    )


class Setup:
    def __init__(self, allowed: Sequence[str] = ("acme",)) -> None:
        self.store = Store()
        self.directory = FakeDirectory()
        self.clock = FakeClock()
        self.use_case = SyncInstallation(
            uow_factory=self.store.uow,
            directory=self.directory,
            clock=self.clock,
            ids=SequentialIds(),
            allowed_accounts=allowed,
        )

    def sync(self, installation_id: int = 10) -> SyncOutcome:
        return self.use_case.execute(SyncInstallationCommand(installation_id))

    @property
    def orgs(self):  # type: ignore[no-untyped-def]
        return list(self.store.state.organizations.values())

    @property
    def repos(self):  # type: ignore[no-untyped-def]
        return sorted(self.store.state.repositories.values(), key=lambda r: r.github_repo_id)

    def actions(self) -> list[str]:
        return [e.action for e in self.store.state.audit]


class TestRegistration:
    def test_registers_allowed_account_with_enabled_repositories(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.directory.repositories[10] = [repo(1, "api"), repo(2, "web", "trunk")]
        assert s.sync() is SyncOutcome.REGISTERED
        (org,) = s.orgs
        assert (org.account_login, org.github_account_id, org.is_active) == ("Acme", 500, True)
        assert [(r.ref.name, r.enabled, r.default_branch.value) for r in s.repos] == [
            ("api", True, "main"),
            ("web", True, "trunk"),
        ]
        assert s.actions() == ["organization.registered"]

    def test_account_outside_allowlist_registers_nothing(self) -> None:
        s = Setup(allowed=("other",))
        s.directory.installations[10] = info()
        assert s.sync() is SyncOutcome.IGNORED
        assert s.orgs == []
        assert "repos" not in s.directory.calls

    def test_empty_allowlist_registers_nothing(self) -> None:
        s = Setup(allowed=())
        s.directory.installations[10] = info()
        assert s.sync() is SyncOutcome.IGNORED
        assert s.orgs == []

    def test_allowlist_is_case_insensitive(self) -> None:
        s = Setup(allowed=("ACME",))
        s.directory.installations[10] = info(login="acme")
        assert s.sync() is SyncOutcome.REGISTERED

    def test_unknown_installation_is_unknown(self) -> None:
        assert Setup().sync(99) is SyncOutcome.UNKNOWN

    def test_github_failure_propagates_and_changes_nothing(self) -> None:
        s = Setup()
        s.directory.fail = True
        with pytest.raises(TransientError):
            s.sync()
        assert s.orgs == []

    def test_is_idempotent(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.directory.repositories[10] = [repo(1, "api")]
        s.sync()
        assert s.sync() is SyncOutcome.UPDATED
        assert len(s.orgs) == 1
        assert len(s.repos) == 1
        assert s.actions() == ["organization.registered"]


class TestRepositories:
    def test_removed_repositories_are_disabled_not_deleted(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.directory.repositories[10] = [repo(1, "api"), repo(2, "web")]
        s.sync()
        s.directory.repositories[10] = [repo(1, "api")]
        s.sync()
        assert [(r.github_repo_id, r.enabled) for r in s.repos] == [(1, True), (2, False)]

    def test_choice_of_the_admin_is_preserved(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.directory.repositories[10] = [repo(1, "api")]
        s.sync()
        original = s.repos[0]
        s.store.state.repositories[original.id] = replace(original, enabled=False)
        s.sync()
        assert s.repos[0].enabled is False

    def test_rename_and_default_branch_change_are_applied(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.directory.repositories[10] = [repo(1, "api")]
        s.sync()
        s.directory.repositories[10] = [repo(1, "api-v2", "develop")]
        s.sync()
        (r,) = s.repos
        assert (r.ref.name, r.default_branch.value) == ("api-v2", "develop")

    def test_repository_of_another_organization_is_not_moved(self) -> None:
        s = Setup(allowed=("acme", "beta"))
        s.directory.installations[10] = info(10, account_id=500, login="acme")
        s.directory.installations[11] = info(11, account_id=600, login="beta")
        s.directory.repositories[10] = [repo(1, "api")]
        s.sync(10)
        s.directory.repositories[11] = [repo(1, "api", owner="beta")]
        s.sync(11)
        (r,) = s.repos
        assert r.organization_id == s.orgs[0].id
        assert "repository.conflict" in s.actions()

    def test_duplicates_in_the_remote_list_are_ignored(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.directory.repositories[10] = [repo(1, "api"), repo(1, "api")]
        s.sync()
        assert len(s.repos) == 1


class TestLifecycle:
    def test_suspension_keeps_repositories_untouched(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.directory.repositories[10] = [repo(1, "api")]
        s.sync()
        s.directory.installations[10] = info(suspended=True)
        assert s.sync() is SyncOutcome.SUSPENDED
        assert s.orgs[0].status is OrganizationStatus.SUSPENDED
        assert s.repos[0].enabled is True
        s.directory.installations[10] = info()
        s.sync()
        assert s.orgs[0].is_active
        assert s.actions() == [
            "organization.registered",
            "organization.suspended",
            "organization.activated",
        ]

    def test_uninstall_marks_org_and_starts_grace_period(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.sync()
        del s.directory.installations[10]
        assert s.sync() is SyncOutcome.UNINSTALLED
        assert s.orgs[0].status is OrganizationStatus.UNINSTALLED
        assert s.store.state.uninstalled_at[s.orgs[0].id] == s.clock.now()
        later = s.clock.now()
        s.clock.advance(days=3)
        s.sync()
        assert s.store.state.uninstalled_at[s.orgs[0].id] == later
        assert s.actions().count("organization.uninstalled") == 1

    def test_reinstall_within_grace_recovers_the_same_organization(self) -> None:
        s = Setup()
        s.directory.installations[10] = info(10)
        s.directory.repositories[10] = [repo(1, "api")]
        s.sync(10)
        org_id = s.orgs[0].id
        del s.directory.installations[10]
        s.sync(10)
        s.directory.installations[20] = info(20)
        s.directory.repositories[20] = [repo(1, "api")]
        assert s.sync(20) is SyncOutcome.REGISTERED
        (org,) = s.orgs
        assert (org.id, org.github_installation_id, org.is_active) == (org_id, 20, True)
        assert org_id not in s.store.state.uninstalled_at
        assert s.repos[0].enabled is True

    def test_recycled_login_does_not_inherit_the_organization(self) -> None:
        s = Setup()
        s.directory.installations[10] = info(10, account_id=500, login="acme")
        s.sync(10)
        s.directory.installations[10] = info(10, account_id=999, login="acme")
        assert s.sync(10) is SyncOutcome.IGNORED
        assert s.orgs[0].github_account_id == 500
        assert "installation.account_mismatch" in s.actions()

    def test_login_rename_is_followed_by_account_id(self) -> None:
        s = Setup(allowed=("acme", "acme-new"))
        s.directory.installations[10] = info(10, login="acme")
        s.sync(10)
        s.directory.installations[10] = info(10, login="acme-new")
        s.sync(10)
        assert s.orgs[0].account_login == "acme-new"

    def test_removal_from_allowlist_suspends_an_active_org(self) -> None:
        s = Setup()
        s.directory.installations[10] = info()
        s.sync()
        s.use_case = SyncInstallation(
            uow_factory=s.store.uow,
            directory=s.directory,
            clock=s.clock,
            ids=SequentialIds(),
            allowed_accounts=(),
        )
        assert s.sync() is SyncOutcome.IGNORED
        assert s.orgs[0].status is OrganizationStatus.SUSPENDED


class TestReconcile:
    def test_queues_remote_and_known_installations_once(self) -> None:
        s = Setup()
        s.directory.installations[10] = info(10)
        s.sync(10)
        s.directory.installations[30] = info(30, account_id=700)
        queue = FakeSyncQueue()
        reconcile = ReconcileInstallations(
            uow_factory=s.store.uow, directory=s.directory, queue=queue
        )
        assert reconcile.execute() == 2
        assert queue.queued == [10, 30]

    def test_known_but_vanished_installation_is_still_queued(self) -> None:
        s = Setup()
        s.directory.installations[10] = info(10)
        s.sync(10)
        del s.directory.installations[10]
        queue = FakeSyncQueue()
        ReconcileInstallations(
            uow_factory=s.store.uow, directory=s.directory, queue=queue
        ).execute()
        assert queue.queued == [10]
