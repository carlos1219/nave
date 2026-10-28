"""Organizaciones (instalaciones de la GitHub App) y repositorios."""

from dataclasses import dataclass
from enum import Enum
from typing import Final, Self

from nave.domain.errors import InvalidTenantError
from nave.domain.validation import is_int_in_range
from nave.domain.value_objects.enum_parsing import parse_enum
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import OrganizationId, RepositoryId
from nave.domain.value_objects.repo_ref import RepoRef, is_valid_account_login

MAX_GITHUB_ID: Final = 2**63 - 1
_BRANCH_PREFIX: Final = "refs/heads/"


class OrganizationStatus(Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    UNINSTALLED = "UNINSTALLED"

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "organization_status")


@dataclass(frozen=True, slots=True, kw_only=True)
class Organization:
    id: OrganizationId
    github_installation_id: int
    account_login: str
    status: OrganizationStatus
    github_account_id: int | None = None
    """Identificador inmutable de la cuenta de GitHub. El login puede cambiar y reasignarse
    a otra persona; el id no (ADR 0014)."""

    def __post_init__(self) -> None:
        if not is_int_in_range(self.github_installation_id, 1, MAX_GITHUB_ID):
            raise InvalidTenantError("github_installation_id", "must be a positive integer")
        if self.github_account_id is not None and not is_int_in_range(
            self.github_account_id, 1, MAX_GITHUB_ID
        ):
            raise InvalidTenantError("github_account_id", "must be a positive integer")
        if not is_valid_account_login(self.account_login):
            raise InvalidTenantError("account_login", "is not a valid GitHub account name")

    @property
    def is_active(self) -> bool:
        return self.status is OrganizationStatus.ACTIVE


@dataclass(frozen=True, slots=True, kw_only=True)
class Repository:
    id: RepositoryId
    organization_id: OrganizationId
    github_repo_id: int
    ref: RepoRef
    default_branch: GitRef
    enabled: bool

    def __post_init__(self) -> None:
        if not is_int_in_range(self.github_repo_id, 1, MAX_GITHUB_ID):
            raise InvalidTenantError("github_repo_id", "must be a positive integer")
        if not isinstance(self.enabled, bool):
            raise InvalidTenantError("enabled", "must be a boolean")

    def is_default_branch(self, ref: GitRef) -> bool:
        """Admite la forma corta (`main`) y la completa (`refs/heads/main`)."""
        return _branch_name(ref) == _branch_name(self.default_branch)


def _branch_name(ref: GitRef) -> str:
    return ref.value.removeprefix(_BRANCH_PREFIX)


def can_be_scanned(repository: Repository, organization: Organization) -> bool:
    """Allowlist (§9): organización activa y repositorio habilitado que le pertenece."""
    return (
        organization.is_active
        and repository.enabled
        and repository.organization_id == organization.id
    )
