"""Puertos de identidad y sesiones (ADR 0016)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from nave.application.dto.auth import (
    LoginFlow,
    OidcIdentity,
    SessionRecord,
    UserSummary,
)
from nave.domain.value_objects.identifiers import UserId


class OidcProviderPort(Protocol):
    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        """URL de autorización con PKCE S256. Puede lanzar `IdentityProviderUnavailableError`."""
        ...

    def complete(self, *, code: str, code_verifier: str, nonce: str) -> OidcIdentity:
        """Intercambia el código y valida el ID token. Lanza `AuthenticationError` si algo no
        cuadra (sin detalle) e `IdentityProviderUnavailableError` si el IdP no responde."""
        ...


class SecretSource(Protocol):
    def new_token(self) -> str:
        """Valor aleatorio de al menos 256 bits, seguro en URL y cookies."""
        ...


class UserRepository(Protocol):
    def record_login(self, identity: OidcIdentity, *, new_id: UUID, at: datetime) -> UserId:
        """Crea el usuario si `(issuer, subject)` no existe; actualiza perfil y último acceso."""
        ...

    def get(self, user: UserId) -> UserSummary:
        """Lanza `NotFoundError`."""
        ...

    def list_recent(self, *, limit: int) -> Sequence[UserSummary]:
        """Los de acceso más reciente primero."""
        ...


class SessionRepository(Protocol):
    def add(self, record: SessionRecord) -> None: ...

    def get(self, token_hash: bytes) -> SessionRecord | None: ...

    def touch(self, token_hash: bytes, at: datetime) -> None: ...

    def delete(self, token_hash: bytes) -> bool:
        """`True` si existía."""
        ...

    def delete_expired(self, now: datetime, *, limit: int) -> int: ...

    def list_for_user(self, user: UserId, *, limit: int) -> Sequence[SessionRecord]:
        """Sesiones del usuario, la de actividad más reciente primero (incluye caducadas)."""
        ...

    def delete_for_user(self, user: UserId) -> int:
        """Borra todas las sesiones del usuario y devuelve cuántas había."""
        ...


class LoginFlowRepository(Protocol):
    def add(self, flow: LoginFlow) -> None: ...

    def consume(self, state_hash: bytes, now: datetime) -> LoginFlow | None:
        """Devuelve y borra el flujo si existe y no ha caducado. Un flujo caducado se borra y
        devuelve `None`. Dos consumos del mismo `state` nunca devuelven el flujo dos veces."""
        ...

    def count_live(self, now: datetime) -> int: ...

    def delete_expired(self, now: datetime, *, limit: int) -> int: ...
