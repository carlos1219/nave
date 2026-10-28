from typing import Any

from celery import Celery
from kombu import Exchange, Queue

from nave.infrastructure.queue.names import (
    MAINTENANCE_QUEUE,
    REPORT_QUEUE,
    SCAN_QUEUE,
    TASK_EXECUTE_SCAN,
    TASK_REPORT_SCAN,
)
from nave.infrastructure.queue.schedule import build_beat_schedule
from nave.infrastructure.queue.settings import CelerySettings

_SOCKET_TIMEOUT_S = 10
_CONNECT_TIMEOUT_S = 5


def build_celery_config(settings: CelerySettings) -> dict[str, Any]:
    """Configuración de Celery. Cada valor tiene una razón de seguridad o de fiabilidad;
    `tests/security/test_celery_config.py` fija los que importan."""
    default_exchange = Exchange("nave", type="direct")
    return {
        "broker_url": settings.broker_url,
        # AM-304: solo JSON. Con pickle, quien escriba en Redis ejecuta código en el worker.
        "task_serializer": "json",
        "accept_content": ["json"],
        "result_serializer": "json",
        "event_serializer": "json",
        # §4/§22: Redis no guarda resultados ni estado definitivo.
        "result_backend": None,
        "task_ignore_result": True,
        "task_store_errors_even_if_ignored": False,
        "task_track_started": False,
        "task_send_sent_event": False,
        "worker_send_task_events": False,
        # §10: fiabilidad. La tarea se confirma al terminar; si el worker muere, se reentrega.
        "task_acks_late": True,
        "task_reject_on_worker_lost": True,
        "task_acks_on_failure_or_timeout": True,
        "worker_prefetch_multiplier": 1,
        "worker_cancel_long_running_tasks_on_connection_loss": True,
        "worker_max_tasks_per_child": settings.max_tasks_per_child,
        "worker_disable_rate_limits": True,
        "task_soft_time_limit": int(settings.scan.soft.total_seconds()),
        "task_time_limit": int(settings.scan.hard.total_seconds()),
        # Colas explícitas: un nombre mal escrito falla en lugar de crear una cola nueva.
        "task_create_missing_queues": False,
        "task_queues": tuple(
            Queue(name, default_exchange, routing_key=name)
            for name in (SCAN_QUEUE, REPORT_QUEUE, MAINTENANCE_QUEUE)
        ),
        "task_default_queue": MAINTENANCE_QUEUE,
        "task_default_exchange": "nave",
        "task_default_exchange_type": "direct",
        "task_default_routing_key": MAINTENANCE_QUEUE,
        "task_routes": {
            TASK_EXECUTE_SCAN: {"queue": SCAN_QUEUE},
            TASK_REPORT_SCAN: {"queue": REPORT_QUEUE},
        },
        "broker_connection_retry_on_startup": True,
        "broker_connection_timeout": _CONNECT_TIMEOUT_S,
        "broker_transport_options": {
            "visibility_timeout": int(settings.visibility_timeout.total_seconds()),
            "socket_timeout": _SOCKET_TIMEOUT_S,
            "socket_connect_timeout": _CONNECT_TIMEOUT_S,
            "socket_keepalive": True,
            "health_check_interval": 30,
            "retry_on_timeout": True,
        },
        # Publicar falla rápido: el escaneo ya está en PostgreSQL y el barrido lo reencola.
        "task_publish_retry": True,
        "task_publish_retry_policy": {
            "max_retries": 2,
            "interval_start": 0.1,
            "interval_step": 0.2,
            "interval_max": 0.5,
        },
        # Beat en memoria: el scheduler no escribe un fichero de estado (`shelve` usa pickle)
        # y, al reiniciar, las tareas periódicas idempotentes simplemente se vuelven a lanzar.
        "beat_scheduler": "celery.beat:Scheduler",
        "enable_utc": True,
        "timezone": "UTC",
        "task_protocol": 2,
        # Los logs los configura la plataforma (JSON estructurado), no Celery.
        "worker_hijack_root_logger": False,
        "worker_redirect_stdouts": False,
    }


def create_scheduler_app(settings: CelerySettings) -> Celery:
    """App del servicio `scheduler`: solo conoce la planificación y el broker. No registra
    tareas ni abre la base de datos."""
    app = create_celery_app(settings)
    app.conf.beat_schedule = build_beat_schedule()
    return app


def create_celery_app(settings: CelerySettings) -> Celery:
    # set_as_current=False: sin app global implícita (§5.3, prohibidos los singletons).
    app = Celery("nave", set_as_current=False, fixups=[])
    app.config_from_object(build_celery_config(settings))
    return app
