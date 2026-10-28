"""Registro de operaciones y generación del documento OpenAPI 3.1 (ADR 0017).

Cada ruta de la API se declara una sola vez, con su modelo de entrada y de salida; de esa
declaración salen la ruta de Flask y el documento. Un test compara el documento servido con
`contracts/openapi.json` (deriva) y con el mapa de rutas de Flask (nada sin documentar)."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from flask import Blueprint
from pydantic import BaseModel

from nave.delivery.http.api_models import Problem
from nave.delivery.http.security import CSRF_HEADER, SESSION_COOKIE

API_PREFIX: Final = "/api/v1"
OPENAPI_VERSION: Final = "3.1.0"
API_VERSION: Final = "1.0.0"
_PLACEHOLDER: Final = re.compile(r"\{(\w+)\}")
_SAFE: Final = frozenset({"GET"})
_NO_RESOURCE: Final = frozenset(
    {"/organizations", "/session", "/auth/logout", "/me/sessions", "/me"}
)


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    description: str
    where: str = "query"
    required: bool = False
    schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "string"})


@dataclass(frozen=True, slots=True)
class Operation:
    method: str
    path: str
    """Con llaves, sin el prefijo `/api/v1`: `/scans/{scan_id}`."""
    operation_id: str
    summary: str
    tag: str
    role: str | None = None
    """Rol mínimo en la organización del recurso, o `None` si basta con estar autenticado."""
    request: type[BaseModel] | None = None
    success: Mapping[int, type[BaseModel] | None] = field(default_factory=dict)
    params: Sequence[Param] = ()
    conflict: bool = False
    public: bool = False
    """Sin sesión (login y callback)."""
    produces: Sequence[str] = ()
    """Tipos de un fichero de descarga (sin modelo JSON): la respuesta 200 es binaria."""

    @property
    def flask_rule(self) -> str:
        return _PLACEHOLDER.sub(r"<\1>", f"{API_PREFIX}{self.path}")

    @property
    def mutates(self) -> bool:
        return self.method not in _SAFE


class Registry:
    def __init__(self) -> None:
        self.operations: list[Operation] = []

    def add(self, blueprint: Blueprint, operation: Operation, view: Callable[..., Any]) -> None:
        self.operations.append(operation)
        blueprint.add_url_rule(
            operation.flask_rule.removeprefix(blueprint.url_prefix or ""),
            endpoint=operation.operation_id,
            view_func=view,
            methods=[operation.method],
        )

    def describe(self, operation: Operation) -> None:
        """Documenta una operación cuya ruta de Flask vive en otro módulo."""
        self.operations.append(operation)

    def document(self) -> dict[str, Any]:
        schemas: dict[str, Any] = {}

        def ref(model: type[BaseModel]) -> dict[str, str]:
            schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
            schemas.update(schema.pop("$defs", {}))
            schemas[model.__name__] = schema
            return {"$ref": f"#/components/schemas/{model.__name__}"}

        paths: dict[str, dict[str, Any]] = {}
        for op in sorted(self.operations, key=lambda o: (o.path, o.method)):
            item: dict[str, Any] = {
                "operationId": op.operation_id,
                "summary": op.summary,
                "tags": [op.tag],
                "description": _description(op),
                "parameters": _parameters(op),
                "responses": _responses(op, ref),
                "security": _security(op),
            }
            if op.request is not None:
                item["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": ref(op.request)}},
                }
            paths.setdefault(op.path, {})[op.method.lower()] = item
        ref(Problem)
        return {
            "openapi": OPENAPI_VERSION,
            "info": {
                "title": "NAVE API",
                "version": API_VERSION,
                "description": (
                    "API de NAVE. Autenticación por cookie de sesión (login OIDC en "
                    "`/api/v1/auth/login`); las peticiones que modifican datos exigen además "
                    "la cabecera `Origin` del sitio y `X-CSRF-Token` (se obtiene en "
                    "`/api/v1/session`). Los errores son RFC 7807."
                ),
            },
            "servers": [{"url": API_PREFIX}],
            "paths": paths,
            "components": {
                "schemas": dict(sorted(schemas.items())),
                "securitySchemes": {
                    "sessionCookie": {"type": "apiKey", "in": "cookie", "name": SESSION_COOKIE},
                    "csrfToken": {"type": "apiKey", "in": "header", "name": CSRF_HEADER},
                },
            },
        }


def _security(op: Operation) -> list[dict[str, list[str]]]:
    if op.public:
        return []
    return [{"sessionCookie": [], "csrfToken": []} if op.mutates else {"sessionCookie": []}]


def _description(op: Operation) -> str:
    if op.public:
        return "No requiere sesión."
    who = (
        f"Requiere rol {op.role} o superior en la organización." if op.role else "Requiere sesión."
    )
    return f"{who} Un recurso de otra organización responde 404, igual que uno inexistente."


def _parameters(op: Operation) -> list[dict[str, Any]]:
    return [
        {
            "name": param.name,
            "in": param.where,
            "required": param.required or param.where == "path",
            "description": param.description,
            "schema": dict(param.schema),
        }
        for param in op.params
    ]


def _responses(op: Operation, ref: Callable[[type[BaseModel]], dict[str, str]]) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    for status, model in sorted(op.success.items()):
        entry: dict[str, Any] = {"description": "Correcto"}
        if model is not None:
            entry["content"] = {"application/json": {"schema": ref(model)}}
        elif op.produces:
            binary = {"type": "string", "format": "binary"}
            entry["content"] = {media: {"schema": dict(binary)} for media in op.produces}
        responses[str(status)] = entry
    problem = {"application/problem+json": {"schema": ref(Problem)}}
    errors = {
        429: "Demasiadas peticiones; `Retry-After` indica los segundos de espera",
        503: "Servicio temporalmente no disponible",
    }
    if op.public:
        errors[401] = "Login fallido"
    else:
        errors[400] = "Petición no válida"
        errors[401] = "Sin sesión válida"
        if op.path not in _NO_RESOURCE:
            errors[404] = "No existe o pertenece a otra organización"
    if op.mutates and not op.public:
        errors[403] = "Origin o token CSRF incorrectos, o rol insuficiente"
    if op.conflict:
        errors[409] = "Conflicto con el estado actual"
    for status, text in sorted(errors.items()):
        responses[str(status)] = {"description": text, "content": problem}
    return responses
