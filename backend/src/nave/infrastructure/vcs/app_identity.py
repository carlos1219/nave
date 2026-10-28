"""Identidad de la propia GitHub App (`GET /app`), usada para reconocer sus comentarios.

El comentario de NAVE en un PR se localiza por su marcador, pero el marcador es texto
público: cualquiera con acceso al PR puede escribirlo. Por eso un comentario solo se edita
si lo escribió el bot de esta App (AM-808).
"""

import re
import threading
from dataclasses import dataclass
from typing import Final

from nave.application.errors import VcsRejectedError
from nave.domain.validation import fullmatch_str
from nave.infrastructure.vcs.app_jwt import AppJwtSigner
from nave.infrastructure.vcs.github_http import GithubApiClient, GithubRequest

_SLUG: Final = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", re.ASCII)


@dataclass(frozen=True, slots=True)
class AppIdentity:
    app_id: int
    bot_login: str
    """Login con el que GitHub firma las acciones de la App: `<slug>[bot]`."""


class GithubAppIdentityProvider:
    def __init__(self, *, api: GithubApiClient, signer: AppJwtSigner) -> None:
        self._api = api
        self._signer = signer
        self._identity: AppIdentity | None = None
        self._lock = threading.Lock()

    def get(self) -> AppIdentity:
        with self._lock:
            if self._identity is None:
                self._identity = self._load()
            return self._identity

    def _load(self) -> AppIdentity:
        reply = self._api.send(
            GithubRequest(method="GET", path="/app", bearer=self._signer.token())
        )
        body = reply.body
        if not isinstance(body, dict):
            raise VcsRejectedError("invalid app response")
        app_id, slug = body.get("id"), body.get("slug")
        if (
            not isinstance(app_id, int)
            or isinstance(app_id, bool)
            or app_id < 1
            or not fullmatch_str(_SLUG, slug)
        ):
            raise VcsRejectedError("invalid app response")
        return AppIdentity(app_id=app_id, bot_login=f"{slug}[bot]")
