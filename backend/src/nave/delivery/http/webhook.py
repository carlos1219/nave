"""Endpoint del webhook de GitHub (CLAUDE.md §9, ADR 0015).

Orden fijo: tamaño → firma sobre el cuerpo crudo → cabeceras → JSON estricto → esquema →
caso de uso. Nada se parsea ni toca la base de datos antes de verificar la firma, y todo
fallo de autenticación devuelve la misma respuesta. El controlador no decide nada de
negocio: traduce el evento a un comando y responde 202."""

import logging
import re
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Final, Protocol

from flask import Blueprint, Response, jsonify, request
from pydantic import BaseModel, ValidationError

from nave.application.dto.platform import WebhookReason
from nave.application.errors import ConcurrentModificationError, QueueUnavailableError
from nave.application.ports.installations import InstallationSyncQueuePort
from nave.application.use_cases.handle_github_event import (
    GithubEvent,
    GithubEventCommand,
    HandleGithubEventResult,
    WebhookOutcome,
)
from nave.delivery.http.problems import PROBLEMS, ProblemSpec, problem
from nave.delivery.http.security import hmac_signed
from nave.delivery.http.strict_json import StrictJsonError, loads_strict
from nave.delivery.http.webhook_models import (
    InstallationEvent,
    PullRequestEvent,
    PushEvent,
)
from nave.domain.errors import DomainError
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.infrastructure.vcs.webhook_verifier import WebhookVerifier

_LOG: Final = logging.getLogger("nave.http")
MAX_BODY_BYTES: Final = 5 * 1024 * 1024
_DELIVERY_ID: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.ASCII
)
_INSTALLATION_EVENTS: Final = frozenset({"installation", "installation_repositories", "repository"})
_KNOWN_EVENTS: Final = frozenset({"push", "pull_request", "ping", *_INSTALLATION_EVENTS})


class HandleGithubEventUseCase(Protocol):
    def execute(self, command: GithubEventCommand) -> HandleGithubEventResult: ...


class RecordWebhookEventUseCase(Protocol):
    def execute(self, reason: WebhookReason) -> None: ...


class _Ignore:
    def execute(self, reason: WebhookReason) -> None:
        return None


@dataclass(frozen=True, slots=True)
class WebhookDependencies:
    verifier: WebhookVerifier
    handle_event: HandleGithubEventUseCase
    sync_queue: InstallationSyncQueuePort
    record_event: RecordWebhookEventUseCase = field(default_factory=_Ignore)
    """Recuento de rechazos y duplicados (ADR 0030); acumula y no escribe por petición."""


def webhook_blueprint(deps: WebhookDependencies) -> Blueprint:
    blueprint = Blueprint("webhook", __name__, url_prefix="/api/v1/webhooks")

    @blueprint.post("/github")
    @hmac_signed
    def github() -> Response | tuple[Response, int]:
        body = request.get_data(cache=False)
        if not deps.verifier.verify(body, request.headers.get("X-Hub-Signature-256")):
            deps.record_event.execute(WebhookReason.SIGNATURE)
            return problem(PROBLEMS["unauthorized"])
        if request.mimetype != "application/json":
            deps.record_event.execute(WebhookReason.MEDIA_TYPE)
            return problem(_UNSUPPORTED)
        delivery = request.headers.get("X-GitHub-Delivery", "")
        event = request.headers.get("X-GitHub-Event", "")
        if _DELIVERY_ID.fullmatch(delivery) is None:
            deps.record_event.execute(WebhookReason.MALFORMED)
            return problem(PROBLEMS["bad_request"])
        if event not in _KNOWN_EVENTS:
            return _accepted()  # evento que NAVE no consume: ni se parsea ni se persiste
        try:
            document = loads_strict(body)
            if event in _INSTALLATION_EVENTS:
                _sync_installation(deps, InstallationEvent.model_validate(document))
            elif event != "ping":
                _handle_scan_event(deps, delivery, event, document)
        except (StrictJsonError, ValidationError, DomainError):
            deps.record_event.execute(WebhookReason.MALFORMED)
            return problem(PROBLEMS["bad_request"])
        return _accepted()

    return blueprint


def _accepted() -> tuple[Response, int]:
    return jsonify(status="accepted"), HTTPStatus.ACCEPTED


def _sync_installation(deps: WebhookDependencies, payload: InstallationEvent) -> None:
    # Solo el id: lo demás lo pregunta el worker a GitHub (AM-809). Si el broker no
    # acepta el trabajo se responde 503 (QueueUnavailableError es transitorio): GitHub
    # marca la entrega como fallida y la conciliación diaria lo repara igualmente.
    deps.sync_queue.enqueue_sync(payload.installation.id)


def _handle_scan_event(
    deps: WebhookDependencies, delivery: str, event: str, document: object
) -> None:
    command = _to_command(delivery, event, document)
    try:
        result = deps.handle_event.execute(command)
    except QueueUnavailableError:
        # El escaneo ya está persistido en QUEUED; el barrido de mantenimiento lo reencola.
        _LOG.warning("scan persisted but not queued", extra={"delivery": delivery})
        return
    except ConcurrentModificationError:
        # Otra petición con la misma entrega ganó la carrera: ya está registrada.
        _LOG.info("concurrent duplicate delivery", extra={"delivery": delivery})
        deps.record_event.execute(WebhookReason.DUPLICATE)
        return
    if result.outcome is WebhookOutcome.DUPLICATE:
        deps.record_event.execute(WebhookReason.DUPLICATE)
    _LOG.info("webhook handled", extra={"delivery": delivery, "outcome": result.outcome.value})


def _to_command(delivery: str, event: str, document: object) -> GithubEventCommand:
    model: BaseModel
    if event == "push":
        model = PushEvent.model_validate(document)
        assert isinstance(model, PushEvent)  # noqa: S101 - estrechamiento de tipo
        return GithubEventCommand(
            delivery_id=delivery,
            event=GithubEvent.PUSH,
            action=None,
            installation_id=model.installation.id,
            github_repo_id=model.repository.id,
            commit=CommitSha(model.after),
            ref=GitRef(model.ref),
        )
    model = PullRequestEvent.model_validate(document)
    assert isinstance(model, PullRequestEvent)  # noqa: S101 - estrechamiento de tipo
    return GithubEventCommand(
        delivery_id=delivery,
        event=GithubEvent.PULL_REQUEST,
        action=model.action,
        installation_id=model.installation.id,
        github_repo_id=model.repository.id,
        commit=CommitSha(model.pull_request.head.sha),
        # La ref de PR de GitHub: existe en el repositorio base también para forks.
        ref=GitRef(f"refs/pull/{model.number}/head"),
        pull_request_number=model.number,
        from_fork=model.from_fork,
    )


_UNSUPPORTED = ProblemSpec(
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported-media-type", "Tipo de contenido no admitido"
)
