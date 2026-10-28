"""Esquemas estrictos de los eventos de GitHub que NAVE consume (AM-206).

Solo se declaran los campos que se usan: identificadores numéricos, SHA, ref y número de
PR. Nada de URLs, nombres, mensajes de commit ni textos del autor: el resto del payload
se ignora y nunca llega a un caso de uso."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

_ID = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]
_SHA = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _IdOnly(_Strict):
    id: _ID


class PushEvent(_Strict):
    ref: Annotated[StrictStr, Field(min_length=1, max_length=255)]
    after: _SHA
    deleted: StrictBool = False
    installation: _IdOnly
    repository: _IdOnly


class _Head(_Strict):
    sha: _SHA
    # `repo` es null si el fork de origen se borró: se trata como fork.
    repo: _IdOnly | None = None


class _Base(_Strict):
    repo: _IdOnly


class _PullRequest(_Strict):
    head: _Head
    base: _Base


class PullRequestEvent(_Strict):
    action: Annotated[StrictStr, Field(pattern=r"^[a-z_]{1,32}$")]
    number: Annotated[StrictInt, Field(ge=1, le=2**31 - 1)]
    installation: _IdOnly
    repository: _IdOnly
    pull_request: _PullRequest

    @property
    def from_fork(self) -> bool:
        head_repo = self.pull_request.head.repo
        return head_repo is None or head_repo.id != self.pull_request.base.repo.id


class InstallationEvent(_Strict):
    """`installation`, `installation_repositories` y `repository`: solo importa qué
    instalación cambió. Qué cambió lo pregunta el worker a GitHub (ADR 0014)."""

    installation: _IdOnly
