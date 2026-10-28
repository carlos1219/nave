from uuid import UUID

import pytest

from nave.domain.entities.tenancy import (
    Organization,
    OrganizationStatus,
    Repository,
    can_be_scanned,
)
from nave.domain.errors import InvalidTenantError
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import OrganizationId
from nave.domain.value_objects.repo_ref import RepoRef
from tests.unit.domain.builders import REPOSITORY_ID

ORG_ID = OrganizationId(UUID("00000000-0000-4000-8000-0000000000c1"))
OTHER_ORG_ID = OrganizationId(UUID("00000000-0000-4000-8000-0000000000c2"))


def org(status: OrganizationStatus = OrganizationStatus.ACTIVE) -> Organization:
    return Organization(id=ORG_ID, github_installation_id=42, account_login="acme", status=status)


def repo(*, enabled: bool = True, organization_id: OrganizationId = ORG_ID) -> Repository:
    return Repository(
        id=REPOSITORY_ID,
        organization_id=organization_id,
        github_repo_id=1001,
        ref=RepoRef("acme", "api"),
        default_branch=GitRef("main"),
        enabled=enabled,
    )


def test_scannable_only_when_active_enabled_and_owned() -> None:
    assert can_be_scanned(repo(), org())
    assert not can_be_scanned(repo(enabled=False), org())
    assert not can_be_scanned(repo(), org(OrganizationStatus.SUSPENDED))
    assert not can_be_scanned(repo(organization_id=OTHER_ORG_ID), org())


@pytest.mark.parametrize(
    ("ref", "expected"),
    [("main", True), ("refs/heads/main", True), ("refs/heads/dev", False), ("main2", False)],
)
def test_default_branch_detection(ref: str, expected: bool) -> None:
    assert repo().is_default_branch(GitRef(ref)) is expected


@pytest.mark.parametrize("installation", [0, -1, True])
def test_invalid_installation_id(installation: int) -> None:
    with pytest.raises(InvalidTenantError):
        Organization(
            id=ORG_ID,
            github_installation_id=installation,
            account_login="acme",
            status=OrganizationStatus.ACTIVE,
        )


def test_invalid_account_login() -> None:
    with pytest.raises(InvalidTenantError):
        Organization(
            id=ORG_ID,
            github_installation_id=1,
            account_login="-bad",
            status=OrganizationStatus.ACTIVE,
        )


@pytest.mark.parametrize(("github_id", "enabled"), [(0, True), (1, "yes")])
def test_invalid_repository_fields(github_id: int, enabled: bool) -> None:
    with pytest.raises(InvalidTenantError):
        Repository(
            id=REPOSITORY_ID,
            organization_id=ORG_ID,
            github_repo_id=github_id,
            ref=RepoRef("acme", "api"),
            default_branch=GitRef("main"),
            enabled=enabled,
        )


def test_status_parse() -> None:
    assert OrganizationStatus.parse("UNINSTALLED") is OrganizationStatus.UNINSTALLED
