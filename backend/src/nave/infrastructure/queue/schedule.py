"""Planificación de tareas periódicas (ADR 0008), definida en código: no se puede cambiar
en tiempo de ejecución ni desde Redis. `expires` descarta ejecuciones que nadie atendió, para
que una caída del worker no deje una avalancha de tareas atrasadas al volver."""

from datetime import timedelta
from typing import Any, Final

from celery.schedules import crontab

from nave.infrastructure.queue.names import (
    MAINTENANCE_QUEUE,
    TASK_HEALTH_CHECK,
    TASK_PURGE_EXPIRED_DATA,
    TASK_PURGE_ORGANIZATIONS,
    TASK_RECONCILE_INSTALLATIONS,
    TASK_RELAY_OUTBOX,
    TASK_SCRUB_USERS,
    TASK_SWEEP_SCANS,
)

RELAY_EVERY: Final = timedelta(seconds=10)
SWEEP_EVERY: Final = timedelta(minutes=5)
PURGE_EXPIRES: Final = timedelta(hours=6)
RECONCILE_EXPIRES: Final = timedelta(hours=6)
# Latido de salud (ADR 0030): la API da por caído al worker a los 3 minutos sin él.
HEALTH_EVERY: Final = timedelta(minutes=1)


def build_beat_schedule() -> dict[str, dict[str, Any]]:
    def options(expires: timedelta) -> dict[str, Any]:
        return {"queue": MAINTENANCE_QUEUE, "expires": expires.total_seconds()}

    return {
        "relay-outbox": {
            "task": TASK_RELAY_OUTBOX,
            "schedule": RELAY_EVERY.total_seconds(),
            "options": options(RELAY_EVERY),
        },
        "sweep-stuck-scans": {
            "task": TASK_SWEEP_SCANS,
            "schedule": SWEEP_EVERY.total_seconds(),
            "options": options(SWEEP_EVERY),
        },
        "purge-uninstalled-organizations": {
            "task": TASK_PURGE_ORGANIZATIONS,
            "schedule": crontab(hour="3", minute="41"),
            "options": options(PURGE_EXPIRES),
        },
        "scrub-orphaned-users": {
            "task": TASK_SCRUB_USERS,
            "schedule": crontab(hour="3", minute="55"),
            "options": options(PURGE_EXPIRES),
        },
        "reconcile-installations": {
            "task": TASK_RECONCILE_INSTALLATIONS,
            "schedule": crontab(hour="4", minute="13"),
            "options": options(RECONCILE_EXPIRES),
        },
        "health-check": {
            "task": TASK_HEALTH_CHECK,
            "schedule": HEALTH_EVERY.total_seconds(),
            "options": options(HEALTH_EVERY),
        },
        "purge-expired-data": {
            "task": TASK_PURGE_EXPIRED_DATA,
            "schedule": crontab(hour="3", minute="27"),
            "options": options(PURGE_EXPIRES),
        },
    }
