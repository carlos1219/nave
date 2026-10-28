import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Final

from nave.domain.entities.finding import Finding
from nave.domain.entities.tenancy import OrganizationStatus, Repository
from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str, is_int_in_range, is_str_with_length
from nave.domain.value_objects.identifiers import OrganizationId, RepositoryId, ScanId, UserId
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.text import is_safe_text
from nave.domain.value_objects.triage_state import TriageState

MAX_PAGE_SIZE: Final = 200
_CURSOR: Final = re.compile(r"[A-Za-z0-9_-]{1,256}", re.ASCII)


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Paginación obligatoria con tope (CLAUDE.md §11). El cursor es opaco."""

    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not is_int_in_range(self.limit, 1, MAX_PAGE_SIZE):
            raise InvalidValueError("limit", f"must be in [1, {MAX_PAGE_SIZE}]")
        if self.cursor is not None and not fullmatch_str(_CURSOR, self.cursor):
            raise InvalidValueError("cursor", "is not a valid cursor")


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FindingQuery:
    repository_id: RepositoryId
    severities: frozenset[Severity] = field(default_factory=frozenset)
    states: frozenset[TriageState] = field(default_factory=frozenset)
    scanner_kinds: frozenset[ScannerKind] = field(default_factory=frozenset)
    seen_in_scan: ScanId | None = None
    only_new: bool = False

    def __post_init__(self) -> None:
        if self.only_new and self.seen_in_scan is None:
            raise InvalidValueError("only_new", "requires seen_in_scan")


@dataclass(frozen=True, slots=True)
class FindingView:
    finding: Finding
    occurrence: FindingOccurrence | None


class LogLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


MAX_LOG_MESSAGE: Final = 4096


@dataclass(frozen=True, slots=True)
class ScanLogEntry:
    seq: int
    at: datetime
    level: LogLevel
    message: str

    def __post_init__(self) -> None:
        if not is_str_with_length(self.message, 1, MAX_LOG_MESSAGE):
            raise InvalidValueError("message", f"length must be in [1, {MAX_LOG_MESSAGE}]")
        if not is_safe_text(self.message):
            raise InvalidValueError("message", "contains control or format characters")


@dataclass(frozen=True, slots=True)
class MembershipView:
    """Una organización a la que pertenece el usuario, con su rol."""

    organization_id: OrganizationId
    account_login: str
    status: OrganizationStatus
    role: Role


@dataclass(frozen=True, slots=True)
class OrganizationOverview:
    """Organización del usuario con sus hallazgos abiertos (triage `OPEN`) por severidad."""

    membership: MembershipView
    open_findings: SeverityCounts


@dataclass(frozen=True, slots=True)
class RepositoryOverview:
    """Repositorio con sus hallazgos abiertos (triage `OPEN`) por severidad."""

    repository: Repository
    open_findings: SeverityCounts


@dataclass(frozen=True, slots=True)
class MemberView:
    """Un miembro de una organización. Solo lo ven los ADMIN de esa organización."""

    user_id: UserId
    display_name: str | None
    email: str | None
    role: Role
