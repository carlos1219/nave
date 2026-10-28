"""Errores HTTP uniformes (RFC 7807, CLAUDE.md §11, ADR 0015).

Un único punto traduce excepciones a respuestas. Nunca se devuelve texto de la excepción,
trazas, rutas internas ni eco de la entrada: solo un `type` estable, un título fijo y el
`request_id` con el que el equipo localiza el registro."""

import logging
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from nave.application.errors import (
    AccessDeniedError,
    ApplicationError,
    AuthenticationError,
    ConcurrentModificationError,
    IdempotencyConflictError,
    LastAdminError,
    NotFoundError,
    RepositoryNotScannableError,
    TransientError,
)
from nave.domain.errors import DomainError

_LOG: Final = logging.getLogger("nave.http")
PROBLEM_JSON: Final = "application/problem+json"
TYPE_PREFIX: Final = "urn:nave:problem:"
RETRY_AFTER_SECONDS: Final = 30


@dataclass(frozen=True, slots=True)
class ProblemSpec:
    status: HTTPStatus
    slug: str
    title: str


_BAD_REQUEST = ProblemSpec(HTTPStatus.BAD_REQUEST, "invalid-request", "Petición no válida")
_UNAUTHORIZED = ProblemSpec(HTTPStatus.UNAUTHORIZED, "unauthorized", "No autenticado")
_FORBIDDEN = ProblemSpec(HTTPStatus.FORBIDDEN, "forbidden", "Acceso denegado")
_NOT_FOUND = ProblemSpec(HTTPStatus.NOT_FOUND, "not-found", "Recurso no encontrado")
_CONFLICT = ProblemSpec(HTTPStatus.CONFLICT, "conflict", "Conflicto con el estado actual")
_UNAVAILABLE = ProblemSpec(
    HTTPStatus.SERVICE_UNAVAILABLE, "unavailable", "Servicio temporalmente no disponible"
)
_TOO_MANY = ProblemSpec(HTTPStatus.TOO_MANY_REQUESTS, "too-many-requests", "Demasiadas peticiones")
_INTERNAL = ProblemSpec(HTTPStatus.INTERNAL_SERVER_ERROR, "internal", "Error interno")

PROBLEMS: Final = {
    "bad_request": _BAD_REQUEST,
    "unauthorized": _UNAUTHORIZED,
    "forbidden": _FORBIDDEN,
    "not_found": _NOT_FOUND,
    "conflict": _CONFLICT,
    "unavailable": _UNAVAILABLE,
    "too_many": _TOO_MANY,
    "internal": _INTERNAL,
}

_BY_STATUS: Final = {
    HTTPStatus.BAD_REQUEST: _BAD_REQUEST,
    HTTPStatus.UNAUTHORIZED: _UNAUTHORIZED,
    HTTPStatus.FORBIDDEN: _FORBIDDEN,
    HTTPStatus.NOT_FOUND: _NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: ProblemSpec(
        HTTPStatus.METHOD_NOT_ALLOWED, "method-not-allowed", "Método no permitido"
    ),
    HTTPStatus.CONFLICT: _CONFLICT,
    HTTPStatus.LENGTH_REQUIRED: ProblemSpec(
        HTTPStatus.LENGTH_REQUIRED, "length-required", "Falta la longitud del cuerpo"
    ),
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: ProblemSpec(
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "payload-too-large", "Cuerpo demasiado grande"
    ),
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: ProblemSpec(
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported-media-type", "Tipo de contenido no admitido"
    ),
    HTTPStatus.TOO_MANY_REQUESTS: _TOO_MANY,
}


def problem(spec: ProblemSpec, *, retry_after: int | None = None) -> Response:
    body = {
        "type": f"{TYPE_PREFIX}{spec.slug}",
        "title": spec.title,
        "status": int(spec.status),
        "request_id": getattr(g, "request_id", None),
    }
    response = jsonify(body)
    response.status_code = int(spec.status)
    response.headers["Content-Type"] = PROBLEM_JSON
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def spec_for(error: Exception) -> ProblemSpec:  # noqa: PLR0911 - una regla por retorno
    if isinstance(error, AuthenticationError):
        return _UNAUTHORIZED
    if isinstance(error, AccessDeniedError):
        # Solo lo lanza el `Authorizer` para quien SÍ pertenece a la organización: saber que
        # el recurso existe no revela nada nuevo. Los ajenos reciben `NotFoundError` (AM-106).
        return _FORBIDDEN
    if isinstance(error, NotFoundError):
        return _NOT_FOUND
    if isinstance(
        error,
        ConcurrentModificationError
        | IdempotencyConflictError
        | LastAdminError
        | RepositoryNotScannableError,
    ):
        return _CONFLICT
    if isinstance(error, TransientError):
        return _UNAVAILABLE
    if isinstance(error, DomainError):
        return _BAD_REQUEST
    return _INTERNAL


def install_error_handling(app: Flask) -> None:
    @app.before_request
    def assign_request_id() -> None:
        # Un identificador propio por petición: nunca se acepta uno del cliente (inyección
        # en logs). Se devuelve en la respuesta para correlacionar con el registro.
        g.request_id = uuid.uuid4().hex

    @app.after_request
    def harden(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["X-Request-Id"] = getattr(g, "request_id", "")
        return response

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> Response:
        status = HTTPStatus(error.code or 500)
        spec = _BY_STATUS.get(status)
        if spec is None:
            spec = ProblemSpec(status, "http-error", status.phrase)
        return problem(spec)

    @app.errorhandler(ApplicationError)
    @app.errorhandler(DomainError)
    def application_error(error: Exception) -> Response:
        spec = spec_for(error)
        if spec.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            _LOG.warning(
                "request failed",
                extra={"error": type(error).__name__, "request_id": g.get("request_id")},
            )
        retry = RETRY_AFTER_SECONDS if spec is _UNAVAILABLE else None
        return problem(spec, retry_after=retry)

    @app.errorhandler(Exception)
    def unexpected(error: Exception) -> Response:
        # Solo el tipo: el mensaje de una excepción interna puede contener rutas o datos.
        _LOG.error(
            "unhandled error",
            extra={
                "error": type(error).__name__,
                "request_id": g.get("request_id"),
                "path": request.path[:200],
            },
        )
        return problem(_INTERNAL)
