"""Datos del login y de las sesiones web (ADR 0016). Sin lógica de red ni de SQL."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.timestamps import ensure_utc
from nave.domain.validation import fullmatch_str, is_str_with_length
from nave.domain.value_objects.identifiers import UserId
from nave.domain.value_objects.text import is_safe_text

_MAX_ISSUER: Final = 255
_MAX_SUBJECT: Final = 255
_MAX_NAME: Final = 255
_MAX_EMAIL: Final = 320
_EMAIL: Final = re.compile(r"[^\s@]{1,64}@[^\s@]{1,255}")


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    absolute: timedelta
    idle: timedelta
    touch_interval: timedelta = timedelta(seconds=60)
    """Como mucho una escritura de `last_seen_at` por sesión en este intervalo."""

    def __post_init__(self) -> None:
        if self.absolute <= timedelta(0) or self.idle <= timedelta(0):
            raise InvalidValueError("session policy", "durations must be positive")
        if self.idle > self.absolute:
            raise InvalidValueError("session policy", "idle timeout cannot exceed the absolute one")


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    """Identidad ya validada por el IdP: `(issuer, subject)` es la clave (ADR 0004)."""

    issuer: str
    subject: str
    display_name: str | None = None
    email: str | None = None
    """Solo se rellena si el IdP lo marcó como verificado. Nunca autentica."""

    def __post_init__(self) -> None:
        if not is_str_with_length(self.issuer, 1, _MAX_ISSUER):
            raise InvalidValueError("issuer", f"length must be in [1, {_MAX_ISSUER}]")
        if not is_str_with_length(self.subject, 1, _MAX_SUBJECT) or not is_safe_text(self.subject):
            raise InvalidValueError("subject", "must be short safe text")
        if self.display_name is not None and (
            not is_str_with_length(self.display_name, 1, _MAX_NAME)
            or not is_safe_text(self.display_name)
        ):
            raise InvalidValueError("display_name", "must be short safe text")
        if self.email is not None and (
            not is_str_with_length(self.email, 3, _MAX_EMAIL)
            or not fullmatch_str(_EMAIL, self.email)
            or not is_safe_text(self.email)
        ):
            raise InvalidValueError("email", "must look like an address")


@dataclass(frozen=True, slots=True)
class LoginRedirect:
    url: str
    binding: str
    """Valor de la cookie de enlace del flujo: solo la conoce el navegador que inició el login."""
    max_age: timedelta


@dataclass(frozen=True, slots=True)
class LoginFlow:
    state_hash: bytes
    binding_hash: bytes
    nonce: str
    code_verifier: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        ensure_utc(self.created_at, "created_at")
        ensure_utc(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class SessionRecord:
    token_hash: bytes
    user_id: UserId
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("created_at", "last_seen_at", "expires_at"):
            ensure_utc(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class NewSession:
    token: str
    """Valor de la cookie. Es la única vez que existe en claro en el servidor."""
    expires_at: datetime
    max_age: timedelta


@dataclass(frozen=True, slots=True)
class Principal:
    """Quién hace la petición. Lo construye `Authenticate`; nunca viene del cliente."""

    user_id: UserId
    csrf_token: str
    display_name: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionView:
    """Una sesión abierta del propio usuario. `id` es `public_session_id`, nunca el hash."""

    id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool


@dataclass(frozen=True, slots=True)
class UserSummary:
    id: UserId
    issuer: str
    display_name: str | None
    email: str | None
    last_login_at: datetime | None
    subject: str = ""
    """Solo para el operador por CLI; no sale por la API."""
