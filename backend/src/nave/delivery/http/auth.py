"""Rutas de login, sesión y logout (ADR 0016). Adaptadores delgados."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from flask import Blueprint, g, jsonify, redirect, request
from werkzeug.wrappers import Response

from nave.application.dto.auth import LoginRedirect, NewSession, Principal
from nave.application.errors import AuthenticationError
from nave.delivery.http import rate_limit
from nave.delivery.http.problems import PROBLEMS, problem
from nave.delivery.http.rate_limit import RateLimiter
from nave.delivery.http.security import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    SessionGuard,
    current_principal,
    public,
)

HOME: str = "/"
"""Destino fijo tras el login: nunca se acepta una URL de retorno del cliente."""


class BeginLoginPort(Protocol):
    def execute(self) -> LoginRedirect: ...


class CompleteLoginPort(Protocol):
    def execute(self, *, state: object, code: object, binding: object) -> NewSession: ...


class LogoutPort(Protocol):
    def execute(self, principal: Principal, token: str) -> None: ...


@dataclass(frozen=True, slots=True)
class AuthDependencies:
    begin: BeginLoginPort
    complete: CompleteLoginPort
    logout: LogoutPort
    guard: SessionGuard
    limiter: RateLimiter


def _set_cookie(response: Response, name: str, value: str, max_age: timedelta) -> None:
    response.set_cookie(
        name,
        value,
        max_age=int(max_age.total_seconds()),
        secure=True,
        httponly=True,
        samesite="Lax",
        path="/",
    )


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, secure=True, httponly=True, samesite="Lax", path="/")


def auth_blueprint(deps: AuthDependencies) -> Blueprint:
    blueprint = Blueprint("auth", __name__, url_prefix="/api/v1")

    @blueprint.get("/auth/login")
    @public
    def login() -> Response:
        wait = deps.limiter.take("login", rate_limit.client_key(), rate_limit.LOGIN)
        if wait:
            return rate_limit.too_many(wait)
        started = deps.begin.execute()
        response = redirect(started.url, code=302)
        _set_cookie(response, LOGIN_COOKIE, started.binding, started.max_age)
        return response

    @blueprint.get("/auth/callback")
    @public
    def callback() -> Response:
        wait = deps.limiter.take("login", rate_limit.client_key(), rate_limit.LOGIN)
        if wait:
            return rate_limit.too_many(wait)
        try:
            session = deps.complete.execute(
                state=request.args.get("state"),
                code=request.args.get("code"),
                binding=request.cookies.get(LOGIN_COOKIE),
            )
        except AuthenticationError:
            failed = problem(PROBLEMS["unauthorized"])
            _clear_cookie(failed, LOGIN_COOKIE)
            return failed
        response = redirect(HOME, code=302)
        _set_cookie(response, SESSION_COOKIE, session.token, session.max_age)
        _clear_cookie(response, LOGIN_COOKIE)
        return response

    @blueprint.post("/auth/logout")
    @deps.guard.required
    def logout() -> Response:
        deps.logout.execute(current_principal(), g.session_token)
        response = Response(status=204)
        _clear_cookie(response, SESSION_COOKIE)
        return response

    @blueprint.get("/session")
    @deps.guard.required
    def session() -> Response:
        principal = current_principal()
        return jsonify(
            user={"id": str(principal.user_id), "display_name": principal.display_name},
            csrf_token=principal.csrf_token,
            expires_at=principal.expires_at.isoformat(),
        )

    return blueprint
