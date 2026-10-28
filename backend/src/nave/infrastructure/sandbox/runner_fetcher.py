"""`RepositoryFetcherPort` sobre el `scan-runner` (ADR 0002)."""

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from nave.application.dto.scanning import FetchLimits
from nave.application.dto.workspace import WorkspaceHandle
from nave.application.errors import ApplicationError, RepositoryFetchError, VcsRejectedError
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.sandbox.runner_client import RunnerClient

_LOG = logging.getLogger("nave.sandbox")


class InstallationTokenProvider(Protocol):
    def fetch_token(self, *, installation_id: int, repo: RepoRef) -> AbstractContextManager[str]:
        """Token de instalación de solo lectura de un único repositorio (AM-506).

        Se revoca al salir del contexto: el token solo hace falta durante el clonado, no
        durante el escaneo.
        """
        ...


class RunnerRepositoryFetcher:
    def __init__(
        self,
        *,
        client: RunnerClient,
        tokens: InstallationTokenProvider,
        new_run_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._client = client
        self._tokens = tokens
        self._new_run_id = new_run_id

    @contextmanager
    def checkout(
        self, *, installation_id: int, repo: RepoRef, commit: CommitSha, limits: FetchLimits
    ) -> Iterator[WorkspaceHandle]:
        # Id aleatorio por intento: un reintento nunca reutiliza el workspace de otro.
        run_id = self._new_run_id()
        try:
            try:
                with self._tokens.fetch_token(installation_id=installation_id, repo=repo) as token:
                    self._client.fetch(
                        run_id=run_id, repo=repo, commit=commit, token=token, limits=limits
                    )
            except VcsRejectedError as error:
                # Instalación sin acceso al repositorio o ya desinstalada: reintentar no lo arregla.
                raise RepositoryFetchError("installation_token_rejected") from error
            yield WorkspaceHandle(run_id)
        finally:
            self._release(run_id)

    def _release(self, run_id: str) -> None:
        try:
            self._client.release(run_id=run_id)
        except ApplicationError as error:
            # No se enmascara el error original; el barrido de huérfanos del runner
            # elimina el workspace si la liberación falló (AM-505).
            _LOG.warning("workspace release failed", extra={"run_id": run_id, "error": error.code})
