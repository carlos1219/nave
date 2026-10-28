"""Sondas de salud. `/healthz` no toca ninguna dependencia (¿el proceso responde?);
`/readyz` comprueba PostgreSQL y Redis y solo responde `ok` o `unavailable`, sin motivo."""

import logging
from collections.abc import Callable, Sequence
from typing import Final

from flask import Blueprint, Response, jsonify

from nave.delivery.http.security import public

_LOG: Final = logging.getLogger("nave.http")
type Check = Callable[[], None]
"""Lanza cualquier excepción si la dependencia no está disponible."""


def health_blueprint(checks: Sequence[Check]) -> Blueprint:
    blueprint = Blueprint("health", __name__)

    @blueprint.get("/healthz")
    @public
    def healthz() -> Response:
        return jsonify(status="ok")

    @blueprint.get("/readyz")
    @public
    def readyz() -> tuple[Response, int]:
        for check in checks:
            try:
                check()
            except Exception as error:  # noqa: BLE001 - la sonda no debe propagar nada
                _LOG.warning("readiness check failed", extra={"error": type(error).__name__})
                return jsonify(status="unavailable"), 503
        return jsonify(status="ok"), 200

    return blueprint
