from collections.abc import Sequence
from typing import Protocol

from nave.application.dto.installations import InstallationInfo, RemoteRepository


class InstallationDirectoryPort(Protocol):
    """Fuente de verdad sobre las instalaciones: GitHub, consultado con la credencial de la
    App. El payload de un webhook nunca alimenta esto (ADR 0014)."""

    def get_installation(self, installation_id: int) -> InstallationInfo | None:
        """`None` si la instalación ya no existe (desinstalada)."""
        ...

    def list_repositories(self, installation_id: int) -> Sequence[RemoteRepository]:
        """Repositorios accesibles a la instalación. Lanza si la lista no es completa."""
        ...

    def list_installation_ids(self) -> Sequence[int]:
        """Todas las instalaciones vivas de la App."""
        ...


class InstallationSyncQueuePort(Protocol):
    def enqueue_sync(self, installation_id: int) -> None:
        """Encola la sincronización de una instalación. Repetirla es inocuo.
        Lanza `QueueUnavailableError` si el broker no la acepta."""
        ...
