"""Datos de una instalación de la GitHub App tal y como los informa GitHub (no el webhook)."""

from dataclasses import dataclass
from enum import Enum

from nave.domain.errors import InvalidValueError
from nave.domain.validation import is_int_in_range
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.repo_ref import RepoRef, is_valid_account_login

MAX_GITHUB_ID = 2**63 - 1


@dataclass(frozen=True, slots=True, kw_only=True)
class InstallationInfo:
    installation_id: int
    account_id: int
    account_login: str
    suspended: bool

    def __post_init__(self) -> None:
        for name in ("installation_id", "account_id"):
            if not is_int_in_range(getattr(self, name), 1, MAX_GITHUB_ID):
                raise InvalidValueError(name, "must be a positive integer")
        if not is_valid_account_login(self.account_login):
            raise InvalidValueError("account_login", "is not a valid GitHub account name")


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteRepository:
    github_repo_id: int
    ref: RepoRef
    default_branch: GitRef

    def __post_init__(self) -> None:
        if not is_int_in_range(self.github_repo_id, 1, MAX_GITHUB_ID):
            raise InvalidValueError("github_repo_id", "must be a positive integer")


class SyncOutcome(Enum):
    REGISTERED = "REGISTERED"
    UPDATED = "UPDATED"
    SUSPENDED = "SUSPENDED"
    UNINSTALLED = "UNINSTALLED"
    IGNORED = "IGNORED"
    UNKNOWN = "UNKNOWN"
