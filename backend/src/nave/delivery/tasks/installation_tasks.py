"""Tareas de sincronización de instalaciones (cola `maintenance`, ADR 0014).

`sync_installation` la encola el webhook (solo con el id de la instalación) o la
conciliación diaria; reintenta con backoff si GitHub falla, porque nada más volvería a
dispararla hasta la siguiente conciliación."""

import logging
from dataclasses import dataclass
from typing import Any, Final, Protocol

from celery import Celery

from nave.application.dto.installations import SyncOutcome
from nave.application.errors import VcsRejectedError
from nave.application.use_cases.sync_installation import SyncInstallationCommand
from nave.delivery.tasks.scan_tasks import (
    RETRYABLE_ERRORS,
    CeleryTaskContext,
    TaskContext,
    retry_after_of,
)
from nave.domain.errors import DomainError
from nave.infrastructure.queue.names import (
    MAINTENANCE_QUEUE,
    TASK_RECONCILE_INSTALLATIONS,
    TASK_SYNC_INSTALLATION,
)
from nave.infrastructure.queue.retry import RetryPolicy
from nave.infrastructure.queue.settings import TaskTimeLimits

_LOG: Final = logging.getLogger(__name__)
_MAX_ID: Final = 2**63 - 1


class SyncInstallationUseCase(Protocol):
    def execute(self, command: SyncInstallationCommand) -> SyncOutcome: ...


class ReconcileInstallationsUseCase(Protocol):
    def execute(self) -> int: ...


@dataclass(frozen=True, slots=True)
class InstallationTaskDependencies:
    sync_installation: SyncInstallationUseCase
    reconcile_installations: ReconcileInstallationsUseCase
    retry: RetryPolicy


def run_sync_installation(
    deps: InstallationTaskDependencies, ctx: TaskContext, raw_installation_id: object
) -> None:
    if (
        not isinstance(raw_installation_id, int)
        or isinstance(raw_installation_id, bool)
        or not 0 < raw_installation_id <= _MAX_ID
    ):
        _LOG.error("discarding malformed task message", extra={"task": TASK_SYNC_INSTALLATION})
        return
    fields = {"installation_id": raw_installation_id, "retries": ctx.retries}
    try:
        outcome = deps.sync_installation.execute(SyncInstallationCommand(raw_installation_id))
    except RETRYABLE_ERRORS as error:
        if ctx.retries >= deps.retry.max_retries:
            _LOG.error("installation sync retries exhausted", extra=fields)
            return
        ctx.retry(deps.retry.delay(ctx.retries, retry_after_of(error)), error)
    except (VcsRejectedError, DomainError) as error:
        # GitHub o los datos rechazan la instalación de forma definitiva: reintentar no
        # lo arregla; la conciliación diaria lo vuelve a intentar con datos nuevos.
        _LOG.error("installation sync rejected", extra={**fields, "error": type(error).__name__})
        return
    _LOG.info("installation synced", extra={**fields, "outcome": outcome.value})


def run_reconcile_installations(deps: InstallationTaskDependencies) -> None:
    try:
        queued = deps.reconcile_installations.execute()
    except (*RETRYABLE_ERRORS, VcsRejectedError) as error:
        _LOG.warning("installation reconcile postponed", extra={"error": type(error).__name__})
        return
    _LOG.info("installations reconciled", extra={"count": queued})


def register_installation_tasks(
    app: Celery, deps: InstallationTaskDependencies, *, limits: TaskTimeLimits
) -> None:
    common: dict[str, Any] = {
        "shared": False,
        "ignore_result": True,
        "acks_late": True,
        "reject_on_worker_lost": True,
        "queue": MAINTENANCE_QUEUE,
        "soft_time_limit": int(limits.soft.total_seconds()),
        "time_limit": int(limits.hard.total_seconds()),
    }

    def sync_installation(self: Any, installation_id: object) -> None:
        run_sync_installation(deps, CeleryTaskContext(self), installation_id)

    def reconcile_installations() -> None:
        run_reconcile_installations(deps)

    app.task(name=TASK_SYNC_INSTALLATION, bind=True, max_retries=None, **common)(sync_installation)
    app.task(name=TASK_RECONCILE_INSTALLATIONS, **common)(reconcile_installations)
