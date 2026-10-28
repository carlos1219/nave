"""Tokens de instalación de la GitHub App (ADR 0013, ADR 0014; AM-506, AM-803).

Dos perfiles, siempre limitados a **un repositorio** y a los permisos mínimos:

- `fetch_token`: `contents:read`, sin caché; se revoca en cuanto termina el clonado.
- `report_token`: `checks:write`, `pull_requests:write` (y `security_events:write` solo si
  la subida de SARIF está activada), con caché en memoria y margen antes de caducar.

El token es un valor opaco: no se interpreta su longitud ni su estructura (ADR 0013).
Nunca se registra ni aparece en un `repr` o en un mensaje de error.
"""

import logging
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from nave.application.errors import ApplicationError, VcsRejectedError
from nave.application.ports.system import Clock
from nave.domain.validation import fullmatch_str
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.sandbox.protocol import TOKEN_PATTERN
from nave.infrastructure.vcs.app_jwt import AppJwtSigner
from nave.infrastructure.vcs.github_http import GithubApiClient, GithubRequest

_LOG: Final = logging.getLogger("nave.github")
FETCH_PERMISSIONS: Final = {"contents": "read"}
REFRESH_MARGIN: Final = timedelta(minutes=5)
MAX_CACHE_ENTRIES: Final = 256
# GitHub emite tokens de 1 h; algo muy distinto indica una respuesta que no entendemos.
_MAX_TOKEN_LIFETIME: Final = timedelta(hours=2)
_HTTP_CREATED: Final = 201
_HTTP_NO_CONTENT: Final = 204
_HTTP_UNAUTHORIZED: Final = 401
_LEVELS: Final = {"read": 1, "write": 2, "admin": 3}


@dataclass(frozen=True, slots=True)
class InstallationToken:
    value: str = field(repr=False)
    expires_at: datetime


class GithubInstallationTokens:
    def __init__(
        self,
        *,
        api: GithubApiClient,
        signer: AppJwtSigner,
        clock: Clock,
        sarif_upload: bool,
    ) -> None:
        self._api = api
        self._signer = signer
        self._clock = clock
        self._report_permissions = {
            "checks": "write",
            "pull_requests": "write",
            **({"security_events": "write"} if sarif_upload else {}),
        }
        self._cache: dict[tuple[int, str], InstallationToken] = {}
        self._lock = threading.Lock()

    @contextmanager
    def fetch_token(self, *, installation_id: int, repo: RepoRef) -> Iterator[str]:
        token = self._mint(installation_id, repo, FETCH_PERMISSIONS)
        try:
            yield token.value
        finally:
            self._revoke(token)

    @contextmanager
    def listing_token(self, *, installation_id: int) -> Iterator[str]:
        """Para `GET /installation/repositories`: abarca toda la instalación, por eso solo
        lleva `metadata:read` (implícito) y se revoca al terminar."""
        reply = self._api.send(
            GithubRequest(
                method="POST",
                path=f"/app/installations/{int(installation_id)}/access_tokens",
                bearer=self._signer.token(),
                json_body={"permissions": {"metadata": "read"}},
                expected=frozenset({_HTTP_CREATED}),
            )
        )
        token = self._parse(reply.body)
        granted = reply.body.get("permissions") if isinstance(reply.body, dict) else None
        if not isinstance(granted, dict) or set(granted) - {"metadata"}:
            self._revoke(token)
            raise VcsRejectedError("token scope differs from the one requested")
        try:
            yield token.value
        finally:
            self._revoke(token)

    def report_token(self, *, installation_id: int, repo: RepoRef) -> str:
        key = (installation_id, repo.name.lower())
        now = self._clock.now()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at - REFRESH_MARGIN > now:
                return cached.value
        token = self._mint(installation_id, repo, self._report_permissions)
        with self._lock:
            self._make_room(now)
            self._cache[key] = token
        return token.value

    def invalidate_report_token(self, *, installation_id: int, repo: RepoRef) -> None:
        """Tras un 401 de GitHub: el token se revocó o caducó antes de lo previsto."""
        with self._lock:
            self._cache.pop((installation_id, repo.name.lower()), None)

    def _make_room(self, now: datetime) -> None:
        if len(self._cache) < MAX_CACHE_ENTRIES:
            return
        for key in [k for k, t in self._cache.items() if t.expires_at <= now]:
            del self._cache[key]
        while len(self._cache) >= MAX_CACHE_ENTRIES:
            del self._cache[next(iter(self._cache))]

    def _mint(
        self, installation_id: int, repo: RepoRef, permissions: Mapping[str, str]
    ) -> InstallationToken:
        reply = self._api.send(
            GithubRequest(
                method="POST",
                path=f"/app/installations/{int(installation_id)}/access_tokens",
                bearer=self._signer.token(),
                json_body={"repositories": [repo.name], "permissions": dict(permissions)},
                expected=frozenset({_HTTP_CREATED}),
            )
        )
        token = self._parse(reply.body)
        if not self._scope_is_exact(reply.body, repo, permissions):
            # No se confía en que GitHub aplicara el límite pedido (AM-506): un token más
            # amplio de lo previsto no llega al contenedor de fetch ni a la caché.
            self._revoke(token)
            raise VcsRejectedError("token scope differs from the one requested")
        return token

    def _parse(self, body: object) -> InstallationToken:
        if not isinstance(body, dict):
            raise VcsRejectedError("invalid token response")
        value, expires = body.get("token"), body.get("expires_at")
        if not isinstance(value, str) or not fullmatch_str(TOKEN_PATTERN, value):
            raise VcsRejectedError("invalid token response")
        expires_at = _parse_instant(expires)
        now = self._clock.now()
        if expires_at is None or not now < expires_at <= now + _MAX_TOKEN_LIFETIME:
            raise VcsRejectedError("invalid token expiry")
        return InstallationToken(value, expires_at)

    @staticmethod
    def _scope_is_exact(body: object, repo: RepoRef, requested: Mapping[str, str]) -> bool:
        if not isinstance(body, dict) or body.get("repository_selection") != "selected":
            return False
        repositories = body.get("repositories")
        if repositories is not None:
            names = (
                [r.get("name") for r in repositories if isinstance(r, dict)]
                if isinstance(repositories, list)
                else []
            )
            if len(names) != 1 or str(names[0]).lower() != repo.name.lower():
                return False
        granted = body.get("permissions")
        if not isinstance(granted, dict):
            return False
        for name, level in granted.items():
            if name == "metadata" and level == "read":
                continue  # permiso implícito de toda instalación
            wanted = requested.get(str(name))
            if wanted is None or _LEVELS.get(str(level), 99) > _LEVELS[wanted]:
                return False
        return True

    def _revoke(self, token: InstallationToken) -> None:
        """Mejor esfuerzo: si falla, la caducidad de 1 h acota la exposición."""
        try:
            self._api.send(
                GithubRequest(
                    method="DELETE",
                    path="/installation/token",
                    bearer=token.value,
                    expected=frozenset({_HTTP_NO_CONTENT, _HTTP_UNAUTHORIZED}),
                )
            )
        except ApplicationError as error:
            _LOG.warning("installation token revoke failed", extra={"error": error.code})


def _parse_instant(raw: object) -> datetime | None:
    if not isinstance(raw, str) or len(raw) > 40:  # noqa: PLR2004
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
