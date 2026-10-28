"""Tareas Celery del ciclo de vida de un escaneo.

Son adaptadores de entrada delgados (CLAUDE.md §10): validan el mensaje, llaman a un
caso de uso y traducen su resultado a la política de reintentos. No contienen lógica
de negocio. El mensaje lleva solo el ID del escaneo (AM-303); el estado se relee de
PostgreSQL en el caso de uso.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, NoReturn, Protocol

from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from nave.application.errors import ConcurrentModificationError, TransientError
from nave.application.use_cases.abandon_scan import AbandonScanCommand
from nave.application.use_cases.execute_scan import ExecuteScanCommand, ExecuteScanOutcome
from nave.application.use_cases.report_to_vcs import ReportOutcome, ReportToVcsCommand
from nave.domain.errors import DomainError
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.status_reason import ReasonCode
from nave.infrastructure.queue.names import (
    REPORT_QUEUE,
    SCAN_QUEUE,
    TASK_EXECUTE_SCAN,
    TASK_REPORT_SCAN,
)
from nave.infrastructure.queue.retry import RetryPolicy
from nave.infrastructure.queue.settings import TaskTimeLimits

_LOG: Final = logging.getLogger(__name__)

# Un conflicto de versión es transitorio por naturaleza: otro proceso cambió el escaneo
# entre la lectura y la escritura, y repetir el caso de uso relee el estado nuevo.
RETRYABLE_ERRORS: Final = (TransientError, ConcurrentModificationError)


class ExecuteScanUseCase(Protocol):
    def execute(self, command: ExecuteScanCommand) -> ExecuteScanOutcome: ...


class ReportToVcsUseCase(Protocol):
    def execute(self, command: ReportToVcsCommand) -> ReportOutcome: ...


class AbandonScanUseCase(Protocol):
    def execute(self, command: AbandonScanCommand) -> bool: ...


class TaskContext(Protocol):
    """Lo mínimo que la lógica de la tarea necesita de Celery (se fabrica en los tests)."""

    @property
    def retries(self) -> int: ...

    def retry(self, delay: timedelta, error: Exception) -> NoReturn: ...


@dataclass(frozen=True, slots=True)
class ScanTaskDependencies:
    execute_scan: ExecuteScanUseCase
    report_to_vcs: ReportToVcsUseCase
    abandon_scan: AbandonScanUseCase
    retry: RetryPolicy


def run_execute_scan(deps: ScanTaskDependencies, ctx: TaskContext, raw_scan_id: object) -> None:
    scan_id = _parse_scan_id(raw_scan_id, TASK_EXECUTE_SCAN)
    if scan_id is None:
        return
    try:
        outcome = deps.execute_scan.execute(ExecuteScanCommand(scan_id))
    except SoftTimeLimitExceeded:
        _LOG.error("scan task hit its time limit", extra=_fields(scan_id, ctx))
        _abandon(deps, scan_id, ReasonCode.TIMEOUT, "task time limit")
        return
    except RETRYABLE_ERRORS as error:
        _retry_or_abandon(deps, ctx, scan_id, error)
        return
    except Exception as error:
        # Error no previsto: el escaneo no puede quedarse a medias esperando al barrido.
        _LOG.exception("unexpected error in scan task", extra=_fields(scan_id, ctx))
        _abandon(deps, scan_id, ReasonCode.INTERNAL_ERROR, type(error).__name__)
        raise
    _LOG.info(
        "scan task finished",
        extra={**_fields(scan_id, ctx), "outcome": outcome.value},
    )


def run_report_scan(deps: ScanTaskDependencies, ctx: TaskContext, raw_scan_id: object) -> None:
    scan_id = _parse_scan_id(raw_scan_id, TASK_REPORT_SCAN)
    if scan_id is None:
        return
    try:
        outcome = deps.report_to_vcs.execute(ReportToVcsCommand(scan_id))
    except (*RETRYABLE_ERRORS, SoftTimeLimitExceeded) as error:
        if ctx.retries >= deps.retry.max_retries:
            # El resultado ya está persistido; solo falta el aviso a GitHub, que es
            # idempotente y se puede relanzar a mano. No hay nada que abandonar.
            _LOG.error("report retries exhausted", extra=_fields(scan_id, ctx))
            return
        ctx.retry(deps.retry.delay(ctx.retries, retry_after_of(error)), error)
    _LOG.info("report task finished", extra={**_fields(scan_id, ctx), "outcome": outcome.value})


def _retry_or_abandon(
    deps: ScanTaskDependencies, ctx: TaskContext, scan_id: ScanId, error: Exception
) -> None:
    if ctx.retries >= deps.retry.max_retries:
        _LOG.error("scan retries exhausted", extra=_fields(scan_id, ctx))
        code = getattr(error, "code", type(error).__name__)
        _abandon(deps, scan_id, ReasonCode.INTERNAL_ERROR, f"retries exhausted: {code}")
        return
    delay = deps.retry.delay(ctx.retries, retry_after_of(error))
    _LOG.warning(
        "scan task will retry",
        extra={**_fields(scan_id, ctx), "delay_s": delay.total_seconds()},
    )
    ctx.retry(delay, error)


def _abandon(deps: ScanTaskDependencies, scan_id: ScanId, code: ReasonCode, detail: str) -> None:
    try:
        deps.abandon_scan.execute(
            AbandonScanCommand(scan_id=scan_id, reason_code=code, detail=detail)
        )
    except Exception:  # noqa: BLE001 - el fallo original es el que importa; este queda registrado
        _LOG.exception("could not abandon scan", extra={"scan_id": str(scan_id)})


def retry_after_of(error: BaseException) -> timedelta | None:
    return error.retry_after if isinstance(error, TransientError) else None


def _parse_scan_id(raw: object, task_name: str) -> ScanId | None:
    try:
        if not isinstance(raw, str):
            raise TypeError  # noqa: TRY301 - un único camino de rechazo
        return ScanId.parse(raw)
    except (DomainError, TypeError):
        # Mensaje malformado: reintentarlo no lo arregla. Se descarta sin eco del contenido.
        _LOG.error("discarding malformed task message", extra={"task": task_name})
        return None


def _fields(scan_id: ScanId, ctx: TaskContext) -> dict[str, Any]:
    return {"scan_id": str(scan_id), "retries": ctx.retries}


class CeleryTaskContext:
    def __init__(self, task: Any) -> None:
        self._task = task

    @property
    def retries(self) -> int:
        return int(self._task.request.retries or 0)

    def retry(self, delay: timedelta, error: Exception) -> NoReturn:
        raise self._task.retry(exc=error, countdown=delay.total_seconds())


def register_scan_tasks(
    app: Celery,
    deps: ScanTaskDependencies,
    *,
    scan_limits: TaskTimeLimits,
    report_limits: TaskTimeLimits,
) -> None:
    def execute_scan(self: Any, scan_id: object) -> None:
        run_execute_scan(deps, CeleryTaskContext(self), scan_id)

    def report_scan(self: Any, scan_id: object) -> None:
        run_report_scan(deps, CeleryTaskContext(self), scan_id)

    # max_retries=None: la política de agotamiento la aplica `RetryPolicy`, no Celery.
    # shared=False: sin él, Celery copia la tarea a toda app creada después en el proceso
    # (estado global); una app ajena ejecutaría estas dependencias.
    common: dict[str, Any] = {
        "shared": False,
        "bind": True,
        "ignore_result": True,
        "acks_late": True,
        "reject_on_worker_lost": True,
        "max_retries": None,
    }
    app.task(
        name=TASK_EXECUTE_SCAN,
        queue=SCAN_QUEUE,
        soft_time_limit=int(scan_limits.soft.total_seconds()),
        time_limit=int(scan_limits.hard.total_seconds()),
        **common,
    )(execute_scan)
    app.task(
        name=TASK_REPORT_SCAN,
        queue=REPORT_QUEUE,
        soft_time_limit=int(report_limits.soft.total_seconds()),
        time_limit=int(report_limits.hard.total_seconds()),
        **common,
    )(report_scan)
