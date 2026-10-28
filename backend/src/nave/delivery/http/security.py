"""Autenticación de rutas y protección CSRF (ADR 0016, AM-101, AM-102).

Cada vista declara cómo se autentica: `session` (cookie + CSRF en métodos no seguros),
`hmac` (webhook firmado) o `public`. Un test recorre el mapa de rutas y falla si alguna no lo
declara: lo nuevo nace denegado, no abierto.
"""

import functools
import hmac
from collections.abc import Callable
from typing import Any, Final, Protocol

from flask import g, request

from nave.application.dto.auth import Principal
from nave.delivery.http import rate_limit
from nave.delivery.http.problems import PROBLEMS, problem
from nave.delivery.http.rate_limit import RateLimiter

SESSION_COOKIE: Final = "__Host-nave_session"
LOGIN_COOKIE: Final = "__Host-nave_login"
CSRF_HEADER: Final = "X-CSRF-Token"
AUTH_ATTRIBUTE: Final = "nave_auth"
SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_CSRF_LENGTH: Final = 128


def public[F: Callable[..., Any]](view: F) -> F:
    setattr(view, AUTH_ATTRIBUTE, "public")
    return view


def hmac_signed[F: Callable[..., Any]](view: F) -> F:
    setattr(view, AUTH_ATTRIBUTE, "hmac")
    return view


class Authenticator(Protocol):
    def execute(self, token: object) -> Principal: ...


class SessionGuard:
    def __init__(
        self, *, authenticate: Authenticator, public_origin: str, limiter: RateLimiter
    ) -> None:
        self._authenticate = authenticate
        self._origin = public_origin
        self._limiter = limiter

    def required[F: Callable[..., Any]](self, view: F) -> F:
        @functools.wraps(view)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            token = request.cookies.get(SESSION_COOKIE)
            principal = self._authenticate.execute(token)
            unsafe = request.method not in SAFE_METHODS
            bucket = rate_limit.WRITE if unsafe else rate_limit.READ
            wait = self._limiter.take("write" if unsafe else "read", str(principal.user_id), bucket)
            if wait:
                return rate_limit.too_many(wait)
            if unsafe and not self._csrf_ok(principal):
                return problem(PROBLEMS["forbidden"])
            g.principal = principal
            g.session_token = token
            return view(*args, **kwargs)

        setattr(guarded, AUTH_ATTRIBUTE, "session")
        return guarded  # type: ignore[return-value]

    def _csrf_ok(self, principal: Principal) -> bool:
        # Origin exacto (sin él se rechaza: los navegadores lo envían en toda petición no
        # segura entre orígenes y en POST del mismo origen) y token propio de la sesión.
        origin = request.headers.get("Origin")
        sent = request.headers.get(CSRF_HEADER, "")
        if origin is None or not hmac.compare_digest(origin.encode(), self._origin.encode()):
            return False
        if not sent or len(sent) > MAX_CSRF_LENGTH:
            return False
        return hmac.compare_digest(sent.encode(), principal.csrf_token.encode())


def current_principal() -> Principal:
    principal: Principal = g.principal
    return principal
