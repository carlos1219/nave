import logging
from typing import Any, Final

from celery import Celery
from kombu.exceptions import KombuError
from redis.exceptions import RedisError

from nave.application.errors import QueueUnavailableError
from nave.application.ports.messaging import ScanStep
from nave.domain.value_objects.identifiers import ScanId
from nave.infrastructure.queue.names import (
    MAINTENANCE_QUEUE,
    STEP_ROUTES,
    TASK_SYNC_INSTALLATION,
    task_id_for,
)

_LOG: Final = logging.getLogger(__name__)
_BROKER_ERRORS: Final = (KombuError, RedisError, OSError)


class CeleryScanQueue:
    """`ScanQueuePort` sobre Celery/Redis.

    - El mensaje lleva solo el ID del escaneo (AM-303): el worker relee todo de PostgreSQL.
    - `max_queue_depth` acota la cola (AM-307): si está llena, el encolado falla y el
      escaneo queda en QUEUED hasta que el barrido de mantenimiento lo reencole.
    """

    def __init__(self, app: Celery, *, max_queue_depth: int) -> None:
        self._app = app
        self._max_depth = max_queue_depth

    def enqueue(self, step: ScanStep, scan_id: ScanId) -> None:
        task_name, queue = STEP_ROUTES[step]
        try:
            self._ensure_capacity(queue)
            self._app.send_task(
                task_name,
                args=[str(scan_id)],
                task_id=task_id_for(step, scan_id),
                queue=queue,
                serializer="json",
            )
        except QueueUnavailableError:
            raise
        except _BROKER_ERRORS as error:
            raise QueueUnavailableError(type(error).__name__) from error

    def revoke(self, scan_id: ScanId) -> None:
        ids = [task_id_for(step, scan_id) for step in ScanStep]
        try:
            # terminate=False: matar un worker a mitad de un escaneo dejaría el workspace
            # sin limpiar. Una tarea en curso se detiene sola al ver el estado terminal.
            self._app.control.revoke(ids, terminate=False)
        except _BROKER_ERRORS as error:
            _LOG.warning(
                "revoke failed; relying on database state",
                extra={"scan_id": str(scan_id), "error": type(error).__name__},
            )

    def _ensure_capacity(self, queue: str) -> None:
        with self._app.connection_for_write() as connection:
            client: Any = connection.default_channel.client
            depth = client.llen(queue)
        if not isinstance(depth, int):
            raise QueueUnavailableError("unexpected queue depth")
        if depth >= self._max_depth:
            raise QueueUnavailableError("queue is full")


class CeleryInstallationSyncQueue:
    """`InstallationSyncQueuePort`: el mensaje lleva solo el id de la instalación; el
    worker pregunta a GitHub todo lo demás (ADR 0014). La API la usa sin importar las
    tareas: solo conoce el nombre."""

    def __init__(self, app: Celery, *, max_queue_depth: int) -> None:
        self._app = app
        self._max_depth = max_queue_depth

    def enqueue_sync(self, installation_id: int) -> None:
        try:
            with self._app.connection_for_write() as connection:
                client: Any = connection.default_channel.client
                depth = client.llen(MAINTENANCE_QUEUE)
            if not isinstance(depth, int) or depth >= self._max_depth:
                raise QueueUnavailableError("queue is full")  # noqa: TRY301
            self._app.send_task(
                TASK_SYNC_INSTALLATION,
                args=[int(installation_id)],
                queue=MAINTENANCE_QUEUE,
                serializer="json",
            )
        except QueueUnavailableError:
            raise
        except _BROKER_ERRORS as error:
            raise QueueUnavailableError(type(error).__name__) from error
