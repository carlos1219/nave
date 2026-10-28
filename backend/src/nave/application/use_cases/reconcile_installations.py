"""Conciliación diaria: GitHub y la base de datos pueden divergir si se pierde un webhook.

Se encola una sincronización por cada instalación que GitHub conoce y por cada una que
tenemos registrada (para detectar las desinstaladas sin evento)."""

import logging
from typing import Final

from nave.application.errors import QueueUnavailableError
from nave.application.ports.installations import (
    InstallationDirectoryPort,
    InstallationSyncQueuePort,
)
from nave.application.ports.persistence import UnitOfWorkFactory

_LOG: Final = logging.getLogger(__name__)


class ReconcileInstallations:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        directory: InstallationDirectoryPort,
        queue: InstallationSyncQueuePort,
    ) -> None:
        self._uow_factory = uow_factory
        self._directory = directory
        self._queue = queue

    def execute(self) -> int:
        remote = set(self._directory.list_installation_ids())
        with self._uow_factory() as uow:
            known = set(uow.tenant_admin.list_installation_ids())
        queued = 0
        for installation_id in sorted(remote | known):
            try:
                self._queue.enqueue_sync(installation_id)
            except QueueUnavailableError:
                _LOG.warning(
                    "installation sync not queued", extra={"installation_id": installation_id}
                )
                continue
            queued += 1
        return queued
