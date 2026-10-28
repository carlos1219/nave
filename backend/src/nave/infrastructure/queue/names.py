"""Nombres de colas y tareas: contrato compartido entre quien encola (API, worker) y
quien consume (worker). Cambiar un nombre con mensajes en vuelo los deja huérfanos."""

from typing import Final

from nave.application.ports.messaging import ScanStep
from nave.domain.value_objects.identifiers import ScanId

SCAN_QUEUE: Final = "scan"
REPORT_QUEUE: Final = "report"
MAINTENANCE_QUEUE: Final = "maintenance"
ALL_QUEUES: Final = (SCAN_QUEUE, REPORT_QUEUE, MAINTENANCE_QUEUE)

TASK_EXECUTE_SCAN: Final = "nave.scan.execute"
TASK_REPORT_SCAN: Final = "nave.scan.report"
TASK_RELAY_OUTBOX: Final = "nave.maintenance.relay_outbox"
TASK_SWEEP_SCANS: Final = "nave.maintenance.sweep_scans"
TASK_PURGE_EXPIRED_DATA: Final = "nave.maintenance.purge_expired_data"
TASK_PURGE_ORGANIZATIONS: Final = "nave.maintenance.purge_organizations"
TASK_SCRUB_USERS: Final = "nave.maintenance.scrub_orphaned_users"
TASK_HEALTH_CHECK: Final = "nave.maintenance.health_check"
TASK_SYNC_INSTALLATION: Final = "nave.installations.sync"
TASK_RECONCILE_INSTALLATIONS: Final = "nave.installations.reconcile"

STEP_ROUTES: Final = {
    ScanStep.EXECUTE: (TASK_EXECUTE_SCAN, SCAN_QUEUE),
    ScanStep.REPORT: (TASK_REPORT_SCAN, REPORT_QUEUE),
}


def task_id_for(step: ScanStep, scan_id: ScanId) -> str:
    return f"{scan_id}:{step.value}"
