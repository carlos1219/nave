"""Casos de uso de lectura. Todos exigen al menos VIEWER en la organización."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from nave.application.dto.views import FindingQuery, FindingView, Page, PageRequest, ScanLogEntry
from nave.application.errors import NotFoundError
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.services.authorization import Authorizer
from nave.domain.entities.scan import Scan
from nave.domain.errors import InvalidValueError
from nave.domain.validation import is_int_in_range
from nave.domain.value_objects.identifiers import ScanId, UserId
from nave.domain.value_objects.role import Role

MAX_LOG_PAGE: Final = 500


@dataclass(frozen=True, slots=True, kw_only=True)
class ListFindingsQuery:
    actor: UserId
    filters: FindingQuery
    page: PageRequest


class ListFindings:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def execute(self, query: ListFindingsQuery) -> Page[FindingView]:
        with self._uow_factory() as uow:
            repository = uow.tenants.get_repository(query.filters.repository_id)
            self._authorizer.require(
                uow,
                actor=query.actor,
                organization_id=repository.organization_id,
                required=Role.VIEWER,
                target_type="repository",
                target_id=str(repository.id),
            )
            if query.filters.seen_in_scan is not None:
                scan = uow.scans.get(query.filters.seen_in_scan)
                if scan.repository_id != repository.id:
                    # Misma respuesta que si no existiera: un error distinto revelaría que
                    # el escaneo existe en otro repositorio (AM-106).
                    raise NotFoundError("scan")
            return uow.findings.list(query.filters, query.page)


@dataclass(frozen=True, slots=True, kw_only=True)
class GetScanQuery:
    actor: UserId
    scan_id: ScanId


class GetScan:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def execute(self, query: GetScanQuery) -> Scan:
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
            return scan


@dataclass(frozen=True, slots=True, kw_only=True)
class ListScanLogsQuery:
    actor: UserId
    scan_id: ScanId
    after_seq: int = 0
    limit: int = 200

    def __post_init__(self) -> None:
        if not is_int_in_range(self.after_seq, 0, 2**63 - 1):
            raise InvalidValueError("after_seq", "must be a non-negative integer")
        if not is_int_in_range(self.limit, 1, MAX_LOG_PAGE):
            raise InvalidValueError("limit", f"must be in [1, {MAX_LOG_PAGE}]")


class ListScanLogs:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer

    def execute(self, query: ListScanLogsQuery) -> Sequence[ScanLogEntry]:
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
            return uow.scan_logs.list(scan.id, after_seq=query.after_seq, limit=query.limit)
