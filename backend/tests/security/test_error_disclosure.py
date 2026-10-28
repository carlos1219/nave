"""AM-104: las respuestas de error no revelan trazas, rutas, versiones ni la entrada."""

import json

import pytest
from flask import Flask

from nave.application.errors import (
    AccessDeniedError,
    ConcurrentModificationError,
    NotFoundError,
    PersistenceUnavailableError,
)
from nave.delivery.http.app import create_app
from nave.delivery.http.problems import PROBLEM_JSON
from nave.delivery.http.webhook import WebhookDependencies
from nave.domain.errors import InvalidValueError
from nave.infrastructure.vcs.webhook_verifier import WebhookVerifier
from tests.fakes.auth import api_deps
from tests.fakes.installations import FakeSyncQueue


class Boom:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self, _command: object) -> None:
        raise self.error


def app_with(readiness: list, error: Exception | None = None) -> Flask:  # type: ignore[type-arg]
    webhook = WebhookDependencies(
        verifier=WebhookVerifier([b"k" * 40]),
        handle_event=Boom(error or RuntimeError("x")),  # type: ignore[arg-type]
        sync_queue=FakeSyncQueue(),
    )
    return create_app(api_deps(webhook, readiness)[0])


def test_unexpected_exceptions_become_a_generic_500() -> None:
    secret_path = "/srv/nave/secrets/db_password"
    app = app_with([])

    @app.get("/explode")
    def explode() -> str:
        raise RuntimeError(f"cannot open {secret_path} password=hunter2")

    response = app.test_client().get("/explode")
    assert response.status_code == 500
    assert response.mimetype == PROBLEM_JSON
    text = response.get_data(as_text=True)
    assert "hunter2" not in text
    assert secret_path not in text
    assert "Traceback" not in text
    body = json.loads(text)
    assert set(body) == {"type", "title", "status", "request_id"}


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("repository"), 404),
        (AccessDeniedError("denied for user bob in org acme"), 403),
        (ConcurrentModificationError("x"), 409),
        (InvalidValueError("field", "secret reason"), 400),
        (PersistenceUnavailableError("postgres://user:pw@db"), 503),
    ],
)
def test_application_errors_map_to_fixed_problems(error: Exception, status: int) -> None:
    app = app_with([])

    @app.get("/raise")
    def raise_it() -> str:
        raise error

    response = app.test_client().get("/raise")
    assert response.status_code == status
    assert response.mimetype == PROBLEM_JSON
    text = response.get_data(as_text=True)
    for leak in ("bob", "acme", "pw", "secret reason", "field", "postgres"):
        assert leak not in text


def test_not_found_and_method_not_allowed_are_problems_too() -> None:
    client = app_with([]).test_client()
    for response, status in (
        (client.get("/nope"), 404),
        (client.get("/api/v1/webhooks/github"), 405),
    ):
        assert response.status_code == status
        assert response.mimetype == PROBLEM_JSON


def test_role_denial_has_a_fixed_body_without_roles_users_or_organizations() -> None:
    app = app_with([])

    @app.get("/b")
    def forbidden() -> str:
        raise AccessDeniedError("role ADMIN required for bob in acme")

    response = app.test_client().get("/b")
    assert response.status_code == 403
    text = response.get_data(as_text=True)
    assert "ADMIN" not in text
    assert "bob" not in text
    assert set(json.loads(text)) == {"type", "title", "status", "request_id"}


def test_every_response_carries_hardening_headers_and_a_server_made_request_id() -> None:
    client = app_with([]).test_client()
    for path in ("/healthz", "/nope"):
        response = client.get(path, headers={"X-Request-Id": "attacker-injected"})
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        assert "injected" not in response.headers["X-Request-Id"]
        assert len(response.headers["X-Request-Id"]) == 32


def test_readiness_reports_only_ok_or_unavailable() -> None:
    def failing() -> None:
        raise RuntimeError("password authentication failed for user nave_app")

    ok = app_with([lambda: None]).test_client().get("/readyz")
    assert (ok.status_code, ok.get_json()) == (200, {"status": "ok"})
    down = app_with([failing]).test_client().get("/readyz")
    assert down.status_code == 503
    assert down.get_json() == {"status": "unavailable"}
    assert "password" not in down.get_data(as_text=True)


def test_liveness_never_touches_dependencies() -> None:
    def failing() -> None:
        raise RuntimeError

    assert app_with([failing]).test_client().get("/healthz").status_code == 200
