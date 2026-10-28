"""Casos de uso de lectura para la API REST. Cada uno exige el rol mínimo en la organización
a la que pertenece el recurso; sin pertenencia la respuesta es la de «no existe» (AM-106)."""

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nave.application.dto.views import (
    FindingView,
    MemberView,
    OrganizationOverview,
    Page,
    PageRequest,
    RepositoryOverview,
)
from nave.application.errors import NotFoundError
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.services.authorization import Authorizer
from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.errors import InvalidValueError
from nave.domain.quality_gates import PolicyVerdict
from nave.domain.timestamps import ensure_utc
from nave.domain.value_objects.identifiers import (
    FindingId,
    OrganizationId,
    RepositoryId,
    ScanId,
    UserId,
)
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.severity_counts import SeverityCounts

_CURSOR_SEPARATOR: Final = "|"


class ListMyOrganizations:
    """Las organizaciones del usuario con sus hallazgos abiertos. No hace falta autorizar:
    solo ve las suyas, y los recuentos se piden únicamente para esas."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, actor: UserId) -> Sequence[OrganizationOverview]:
        with self._uow_factory() as uow:
            memberships = uow.memberships.list_for_user(actor)
            counts = uow.findings.open_counts_by_organization(
                [m.organization_id for m in memberships]
            )
        return [
            OrganizationOverview(m, counts.get(m.organization_id, SeverityCounts()))
            for m in memberships
        ]


class ListRepositories:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def execute(
        self, *, actor: UserId, organization_id: OrganizationId
    ) -> Sequence[RepositoryOverview]:
        with self._uow_factory() as uow:
            self._authorizer.require(
                uow,
                actor=actor,
                organization_id=organization_id,
                required=Role.VIEWER,
                target_type="organization",
                target_id=str(organization_id),
            )
            repositories = uow.tenants.list_repositories(organization_id)
            counts = uow.findings.open_counts_by_repository(organization_id)
        return [RepositoryOverview(r, counts.get(r.id, SeverityCounts())) for r in repositories]


class ListMembers:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def execute(self, *, actor: UserId, organization_id: OrganizationId) -> Sequence[MemberView]:
        with self._uow_factory() as uow:
            self._authorizer.require(
                uow,
                actor=actor,
                organization_id=organization_id,
                required=Role.ADMIN,
                target_type="organization",
                target_id=str(organization_id),
            )
            return uow.memberships.list_members(organization_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class ListScansQuery:
    actor: UserId
    repository_id: RepositoryId
    page: PageRequest


def encode_scan_cursor(scan: Scan) -> str:
    raw = f"{scan.queued_at.isoformat()}{_CURSOR_SEPARATOR}{scan.id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_scan_cursor(cursor: str) -> tuple[datetime, ScanId]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        stamp, _, ident = raw.partition(_CURSOR_SEPARATOR)
        return ensure_utc(datetime.fromisoformat(stamp), "cursor"), ScanId.parse(ident)
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise InvalidValueError("cursor", "is not a valid cursor") from error


class ListScans:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def execute(self, query: ListScansQuery) -> Page[Scan]:
        before = decode_scan_cursor(query.page.cursor) if query.page.cursor else None
        with self._uow_factory() as uow:
            repository = uow.tenants.get_repository(query.repository_id)
            self._authorizer.require(
                uow,
                actor=query.actor,
                organization_id=repository.organization_id,
                required=Role.VIEWER,
                target_type="repository",
                target_id=str(repository.id),
            )
            scans = uow.scans.list_for_repository(
                repository.id, limit=query.page.limit + 1, before=before
            )
        items = tuple(scans[: query.page.limit])
        more = len(scans) > query.page.limit
        return Page(items, encode_scan_cursor(items[-1]) if more and items else None)


@dataclass(frozen=True, slots=True, kw_only=True)
class GetScanVerdictQuery:
    actor: UserId
    scan_id: ScanId


class GetScanVerdict:
    """Resultado de los quality gates de un escaneo (`None` si aún no se evaluó)."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def execute(self, query: GetScanVerdictQuery) -> PolicyVerdict | None:
        with self._uow_factory() as uow:
            scan = uow.scans.get(query.scan_id)
            repository = uow.tenants.get_repository(scan.repository_id)
            self._authorizer.require(
                uow,
                actor=query.actor,
                organization_id=repository.organization_id,
                required=Role.VIEWER,
                target_type="scan",
                target_id=str(scan.id),
            )
            return uow.policies.get_evaluation(scan.id)


@dataclass(frozen=True, slots=True)
class FindingDetail:
    view: FindingView
    decisions: tuple[TriageDecision, ...]

    @property
    def finding(self) -> Finding:
        return self.view.finding


class GetFinding:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def execute(self, *, actor: UserId, finding_id: FindingId) -> FindingDetail:
        with self._uow_factory() as uow:
            try:
                view = uow.findings.view_of(finding_id)
            except NotFoundError:
                raise NotFoundError("finding") from None
            repository = uow.tenants.get_repository(view.finding.repository_id)
            self._authorizer.require(
                uow,
                actor=actor,
                organization_id=repository.organization_id,
                required=Role.VIEWER,
                target_type="finding",
                target_id=str(finding_id),
            )
            return FindingDetail(view, tuple(uow.findings.decisions_of(finding_id)))
