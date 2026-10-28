"""Directorio de instalaciones sobre la API de GitHub (ADR 0014).

Las instalaciones se consultan con el JWT de la App; los repositorios, con un token de
instalación que solo lleva `metadata:read` y se revoca al terminar. Los datos remotos se
validan uno a uno: un elemento mal formado se descarta y se registra, no rompe la lista.
"""

import logging
from collections.abc import Sequence
from typing import Final

from nave.application.dto.installations import InstallationInfo, RemoteRepository
from nave.application.errors import VcsRejectedError
from nave.domain.errors import DomainError
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.vcs.app_jwt import AppJwtSigner
from nave.infrastructure.vcs.github_http import GithubApiClient, GithubReply, GithubRequest
from nave.infrastructure.vcs.installation_tokens import GithubInstallationTokens

_LOG: Final = logging.getLogger("nave.github")
PAGE_SIZE: Final = 100
# 10 000 repositorios por instalación; más allá la lista no sería completa y deshabilitar
# "los que faltan" sería incorrecto, así que se falla en vez de truncar.
MAX_PAGES: Final = 100
_HTTP_OK: Final = 200
_HTTP_NOT_FOUND: Final = 404


class GithubInstallationDirectory:
    def __init__(
        self, *, api: GithubApiClient, signer: AppJwtSigner, tokens: GithubInstallationTokens
    ) -> None:
        self._api = api
        self._signer = signer
        self._tokens = tokens

    def get_installation(self, installation_id: int) -> InstallationInfo | None:
        reply = self._api.send(
            GithubRequest(
                method="GET",
                path=f"/app/installations/{int(installation_id)}",
                bearer=self._signer.token(),
                expected=frozenset({_HTTP_OK, _HTTP_NOT_FOUND}),
            )
        )
        if reply.status == _HTTP_NOT_FOUND:
            return None
        body = reply.body
        account = body.get("account") if isinstance(body, dict) else None
        if not isinstance(body, dict) or not isinstance(account, dict):
            raise VcsRejectedError("invalid installation response")
        try:
            return InstallationInfo(
                installation_id=_integer(body.get("id")),
                account_id=_integer(account.get("id")),
                account_login=_text(account.get("login")),
                suspended=body.get("suspended_at") is not None,
            )
        except DomainError as error:
            raise VcsRejectedError("invalid installation response") from error

    def list_installation_ids(self) -> Sequence[int]:
        ids: list[int] = []
        for page in range(1, MAX_PAGES + 1):
            reply = self._api.send(
                GithubRequest(
                    method="GET",
                    path=f"/app/installations?per_page={PAGE_SIZE}&page={page}",
                    bearer=self._signer.token(),
                )
            )
            items = _list(reply)
            ids.extend(i["id"] for i in items if isinstance(i, dict) and _is_id(i.get("id")))
            if len(items) < PAGE_SIZE:
                return ids
        raise VcsRejectedError("too many installations")

    def list_repositories(self, installation_id: int) -> Sequence[RemoteRepository]:
        found: list[RemoteRepository] = []
        with self._tokens.listing_token(installation_id=installation_id) as token:
            for page in range(1, MAX_PAGES + 1):
                reply = self._api.send(
                    GithubRequest(
                        method="GET",
                        path=f"/installation/repositories?per_page={PAGE_SIZE}&page={page}",
                        bearer=token,
                    )
                )
                body = reply.body
                items = body.get("repositories") if isinstance(body, dict) else None
                if not isinstance(items, list):
                    raise VcsRejectedError("invalid repositories response")
                found.extend(r for r in map(_repository, items) if r is not None)
                if len(items) < PAGE_SIZE:
                    return found
        raise VcsRejectedError("too many repositories")


def _list(reply: GithubReply) -> list[object]:
    if not isinstance(reply.body, list):
        raise VcsRejectedError("invalid installations response")
    return reply.body


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _integer(value: object) -> int:
    if not _is_id(value):
        raise VcsRejectedError("invalid installation response")
    assert isinstance(value, int)  # noqa: S101 - estrechamiento de tipo tras `_is_id`
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise VcsRejectedError("invalid installation response")
    return value


def _repository(item: object) -> RemoteRepository | None:
    if not isinstance(item, dict):
        return None
    owner = item.get("owner")
    repo_id, name, branch = item.get("id"), item.get("name"), item.get("default_branch")
    try:
        if not isinstance(owner, dict) or not _is_id(repo_id):
            raise VcsRejectedError("invalid repository")  # noqa: TRY301
        return RemoteRepository(
            github_repo_id=_integer(repo_id),
            ref=RepoRef(_text(owner.get("login")), _text(name)),
            default_branch=GitRef(_text(branch)),
        )
    except (DomainError, VcsRejectedError):
        # Nombres o ramas que NAVE no admite (p. ej. caracteres fuera de GitRef): se omiten.
        _LOG.warning("repository skipped: unsupported name or branch")
        return None
