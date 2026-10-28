"""Punto de entrada del servicio `scheduler` (ADR 0008): `celery beat` con la planificación
fijada en código. Solo necesita el broker; no recibe credenciales de base de datos ni de
GitHub, de modo que comprometerlo solo permite encolar tareas de mantenimiento."""

import os
from collections.abc import Mapping

from celery import Celery

from nave.bootstrap.config import Settings
from nave.infrastructure.observability.logging import configure_logging
from nave.infrastructure.queue.celery_app import create_scheduler_app


def build_scheduler(environ: Mapping[str, str]) -> Celery:
    configure_logging("nave-scheduler")
    return create_scheduler_app(Settings.celery_only(environ))


def scheduler_app() -> Celery:
    """Fábrica para `celery --app nave.bootstrap.scheduler:scheduler_app beat`."""
    return build_scheduler(os.environ)
