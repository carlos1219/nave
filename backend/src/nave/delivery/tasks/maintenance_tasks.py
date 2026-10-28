"""Tareas periódicas (cola `maintenance`). Las dispara `celery beat` desde el servicio
`scheduler` (ADR 0008). Cada una es idempotente y no reintenta por sí misma: la siguiente
ejecución programada hace de reintento."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from celery import Celery

from nave.application.dto.maintenance import OrganizationPurgeReport, RetentionReport, SweepReport
from nave.application.dto.platform import ComponentHealth, HealthStatus
from nave.delivery.tasks.scan_tasks import RETRYABLE_ERRORS
from nave.infrastructure.queue.names import (
    MAINTENANCE_QUEUE,
    TASK_HEALTH_CHECK,
    TASK_PURGE_EXPIRED_DATA,
    TASK_PURGE_ORGANIZATIONS,
    TASK_RELAY_OUTBOX,
    TASK_SCRUB_USERS,
    TASK_SWEEP_SCANS,
)
from nave.infrastructure.queue.settings import TaskTimeLimits

_LOG: Final = logging.getLogger(__name__)


class RelayOutboxUseCase(Protocol):
    def execute(self) -> int: ...


class SweepStuckScansUseCase(Protocol):
    def execute(self) -> SweepReport: ...


class PurgeExpiredDataUseCase(Protocol):
    def execute(self) -> RetentionReport: ...


class PurgeOrganizationsUseCase(Protocol):
    def execute(self) -> OrganizationPurgeReport: ...


class ScrubOrphanedUsersUseCase(Protocol):
    def execute(self) -> int: ...


class RecordHealthUseCase(Protocol):
    def execute(self) -> Sequence[ComponentHealth]: ...


@dataclass(frozen=True, slots=True)
class MaintenanceTaskDependencies:
    relay_outbox: RelayOutboxUseCase
    sweep_scans: SweepStuckScansUseCase
    purge_expired_data: PurgeExpiredDataUseCase
    purge_organizations: PurgeOrganizationsUseCase
    scrub_users: ScrubOrphanedUsersUseCase
    record_health: RecordHealthUseCase | None = None


def run_relay_outbox(deps: MaintenanceTaskDependencies) -> None:
    try:
        published = deps.relay_outbox.execute()
    except RETRYABLE_ERRORS as error:
        _LOG.warning("outbox relay postponed", extra={"error": error.code})
        return
    if published:
        _LOG.info("outbox relayed", extra={"count": published})


def run_sweep_scans(deps: MaintenanceTaskDependencies) -> None:
    try:
        report = deps.sweep_scans.execute()
    except RETRYABLE_ERRORS as error:
        _LOG.warning("scan sweep postponed", extra={"error": error.code})
        return
    if any((report.requeued, report.expired_in_queue, report.abandoned_running)):
        _LOG.warning(
            "stuck scans recovered",
            extra={"count": report.requeued + report.expired_in_queue + report.abandoned_running},
        )


def run_purge_expired_data(deps: MaintenanceTaskDependencies) -> None:
    try:
        report = deps.purge_expired_data.execute()
    except RETRYABLE_ERRORS as error:
        _LOG.warning("retention purge postponed", extra={"error": error.code})
        return
    _LOG.info(
        "retention purge finished",
        extra={
            "count": report.webhook_deliveries
            + report.scan_logs
            + report.published_outbox
            + report.scans
        },
    )


def run_purge_organizations(deps: MaintenanceTaskDependencies) -> None:
    try:
        report = deps.purge_organizations.execute()
    except RETRYABLE_ERRORS as error:
        _LOG.warning("organization purge postponed", extra={"error": error.code})
        return
    _LOG.info(
        "organization purge finished",
        extra={
            "purged": report.purged,
            "in_progress": report.in_progress,
            "refused": report.refused,
        },
    )
    if report.refused:
        _LOG.error("organization purge refused by the database", extra={"count": report.refused})


def run_scrub_users(deps: MaintenanceTaskDependencies) -> None:
    try:
        deps.scrub_users.execute()
    except RETRYABLE_ERRORS as error:
        _LOG.warning("user scrub postponed", extra={"error": error.code})


def run_record_health(deps: MaintenanceTaskDependencies) -> None:
    if deps.record_health is None:
        return
    try:
        reports = deps.record_health.execute()
    except RETRYABLE_ERRORS as error:
        _LOG.warning("health check not recorded", extra={"error": error.code})
        return
    down = [r.component.value for r in reports if r.status is HealthStatus.DOWN]
    if down:
        _LOG.warning("components down", extra={"count": len(down)})


def register_maintenance_tasks(
    app: Celery, deps: MaintenanceTaskDependencies, *, limits: TaskTimeLimits
) -> None:
    options: dict[str, Any] = {
        "shared": False,
        "ignore_result": True,
        "acks_late": True,
        "reject_on_worker_lost": True,
        "queue": MAINTENANCE_QUEUE,
        "soft_time_limit": int(limits.soft.total_seconds()),
        "time_limit": int(limits.hard.total_seconds()),
    }

    def relay_outbox() -> None:
        run_relay_outbox(deps)

    def sweep_scans() -> None:
        run_sweep_scans(deps)

    def purge_expired_data() -> None:
        run_purge_expired_data(deps)

    app.task(name=TASK_RELAY_OUTBOX, **options)(relay_outbox)
    app.task(name=TASK_SWEEP_SCANS, **options)(sweep_scans)

    def purge_organizations() -> None:
        run_purge_organizations(deps)

    app.task(name=TASK_PURGE_EXPIRED_DATA, **options)(purge_expired_data)
    app.task(name=TASK_PURGE_ORGANIZATIONS, **options)(purge_organizations)

    def scrub_users() -> None:
        run_scrub_users(deps)

    app.task(name=TASK_SCRUB_USERS, **options)(scrub_users)

    def health_check() -> None:
        run_record_health(deps)

    app.task(name=TASK_HEALTH_CHECK, **options)(health_check)
