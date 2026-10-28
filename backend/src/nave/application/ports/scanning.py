from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from nave.application.dto.scanning import FetchLimits, ScanLimits, ScannerOutput
from nave.application.dto.workspace import WorkspaceHandle
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.scanner_kind import ScannerKind


class RepositoryFetcherPort(Protocol):
    def checkout(
        self, *, installation_id: int, repo: RepoRef, commit: CommitSha, limits: FetchLimits
    ) -> AbstractContextManager[WorkspaceHandle]:
        """Obtiene exactamente `commit` en un workspace aislado del runner.

        El workspace se destruye al salir del contexto, también ante excepciones.
        Aplica el endurecimiento de git de AM-504. Lanza `RepositoryTooLargeError`,
        `RepositoryFetchError` (permanentes) o `FetchUnavailableError` (transitorio).
        """
        ...


class ScannerPort(Protocol):
    @property
    def kind(self) -> ScannerKind: ...

    def scan(self, workspace: WorkspaceHandle, limits: ScanLimits) -> ScannerOutput:
        """Ejecuta el escáner sobre el workspace dentro del sandbox, sin red.

        Nunca usa configuración del repositorio escaneado (AM-503). Lanza
        `ScanTimeoutError`, `ScannerExecutionError`, `ScannerOutputInvalidError`
        (permanentes) o `ScannerUnavailableError` (transitorio).
        """
        ...


class ScannerRegistryPort(Protocol):
    def scanners(self) -> Sequence[ScannerPort]:
        """Escáneres habilitados, en orden estable. Nunca vacío en un despliegue válido."""
        ...
