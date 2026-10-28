"""Fábrica de la aplicación Flask (CLAUDE.md §5.3, §11, ADR 0015).

Recibe las dependencias ya construidas; no instancia nada concreto. Los blueprints son
adaptadores delgados: validan la entrada, llaman a un caso de uso y mapean la respuesta."""

from collections.abc import Sequence
from dataclasses import dataclass

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from nave.delivery.http.api import RestDependencies, rest_blueprint
from nave.delivery.http.auth import AuthDependencies, auth_blueprint
from nave.delivery.http.health import Check, health_blueprint
from nave.delivery.http.problems import install_error_handling
from nave.delivery.http.webhook import MAX_BODY_BYTES, WebhookDependencies, webhook_blueprint


@dataclass(frozen=True, slots=True)
class ApiDependencies:
    webhook: WebhookDependencies
    auth: AuthDependencies
    rest: RestDependencies
    readiness: Sequence[Check]
    trusted_proxy_hops: int = 0
    """Proxies de confianza delante de la API (0 = ninguno: `X-Forwarded-For` se ignora)."""


def create_app(deps: ApiDependencies) -> Flask:
    app = Flask("nave", static_folder=None)
    # Flask corta el cuerpo al superar el tope, también con transferencia fragmentada.
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    app.config["PROPAGATE_EXCEPTIONS"] = False
    if deps.trusted_proxy_hops:
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=deps.trusted_proxy_hops, x_proto=0, x_host=0, x_port=0, x_prefix=0
        )
    install_error_handling(app)
    app.register_blueprint(health_blueprint(deps.readiness))
    app.register_blueprint(webhook_blueprint(deps.webhook))
    app.register_blueprint(auth_blueprint(deps.auth))
    app.register_blueprint(rest_blueprint(deps.rest)[0])
    return app
