import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

import pytest
from flask import Flask
from flask.testing import FlaskClient

from nave.application.dto.platform import WebhookReason
from nave.application.errors import PersistenceUnavailableError, QueueUnavailableError
from nave.delivery.http.app import create_app
from nave.delivery.http.webhook import MAX_BODY_BYTES, WebhookDependencies
from nave.domain.value_objects.scan_status import ScanStatus
from nave.infrastructure.vcs.webhook_verifier import WebhookVerifier
from tests.fakes.auth import api_deps
from tests.fakes.installations import FakeSyncQueue
from tests.unit.application.builders import GITHUB_REPO_ID, INSTALLATION_ID
from tests.unit.application.harness import Harness, make_harness

SECRET = b"k" * 40
DELIVERY = "0a1b2c3d-0000-4000-8000-000000000001"
SHA = "a" * 40
URL = "/api/v1/webhooks/github"
JSON = "application/json"


class World:
    def __init__(self, *, scan_forks: bool = True) -> None:
        self.h: Harness = make_harness(scan_forks=scan_forks)
        self.sync = FakeSyncQueue()
        self.ready: list[Any] = []
        webhook = WebhookDependencies(
            verifier=WebhookVerifier([SECRET]), handle_event=self.h.webhook, sync_queue=self.sync
        )
        self.app: Flask = create_app(api_deps(webhook, self.ready, self.h)[0])
        self.client: FlaskClient = self.app.test_client()

    def post(  # noqa: PLR0913 - doble de test con muchas variantes
        self,
        payload: Any,
        *,
        event: str = "push",
        delivery: str | None = DELIVERY,
        secret: bytes = SECRET,
        content_type: str | None = "application/json",
        raw: bytes | None = None,
        signature: str | None = "auto",
    ) -> Any:
        body = raw if raw is not None else json.dumps(payload).encode()
        headers = {"X-GitHub-Event": event}
        if delivery is not None:
            headers["X-GitHub-Delivery"] = delivery
        if signature == "auto":
            headers["X-Hub-Signature-256"] = (
                "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
            )
        elif signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return self.client.post(URL, data=body, headers=headers, content_type=content_type)

    @property
    def scans(self) -> list[Any]:
        return list(self.h.store.state.scans.values())


def push(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ref": "refs/heads/main",
        "after": SHA,
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": GITHUB_REPO_ID, "full_name": "evil/steal", "clone_url": "http://x"},
        "head_commit": {"message": "hi"},
    }
    payload.update(overrides)
    return payload


def pull_request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "opened",
        "number": 7,
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": GITHUB_REPO_ID},
        "pull_request": {
            "head": {"sha": SHA, "repo": {"id": GITHUB_REPO_ID}},
            "base": {"repo": {"id": GITHUB_REPO_ID}},
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def world() -> World:
    return World()


class TestAuthentication:
    def test_valid_push_creates_and_queues_a_scan(self, world: World) -> None:
        response = world.post(push())
        assert response.status_code == 202
        assert response.get_json() == {"status": "accepted"}
        (scan,) = world.scans
        assert scan.status is ScanStatus.QUEUED
        assert scan.commit.value == SHA
        assert len(world.h.queue.enqueued) == 1

    @pytest.mark.parametrize("signature", [None, "", "sha256=" + "0" * 64, "sha1=abc", "nonsense"])
    def test_bad_or_missing_signature_is_401_and_changes_nothing(
        self, world: World, signature: str | None
    ) -> None:
        response = world.post(push(), signature=signature)
        assert response.status_code == 401
        assert response.mimetype == "application/problem+json"
        assert world.scans == []
        assert world.h.store.state.deliveries == {}

    def test_signature_with_another_secret(self, world: World) -> None:
        assert world.post(push(), secret=b"x" * 40).status_code == 401

    def test_all_authentication_failures_look_identical(self, world: World) -> None:
        a = world.post(push(), signature=None).get_json()
        b = world.post(push(), secret=b"z" * 40).get_json()
        assert {k: v for k, v in a.items() if k != "request_id"} == {
            k: v for k, v in b.items() if k != "request_id"
        }

    def test_signature_is_checked_before_anything_is_parsed(self, world: World) -> None:
        response = world.post(None, raw=b"\xff not json", signature="sha256=" + "0" * 64)
        assert response.status_code == 401

    def test_tampered_body_fails(self, world: World) -> None:
        body = json.dumps(push()).encode()
        good = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        tampered = body.replace(SHA.encode(), b"b" * 40)
        response = world.post(None, raw=tampered, signature=good)
        assert response.status_code == 401


class TestEnvelope:
    def test_oversized_body_is_413_before_anything_else(self, world: World) -> None:
        response = world.post(None, raw=b"x" * (MAX_BODY_BYTES + 1))
        assert response.status_code == 413
        assert response.mimetype == "application/problem+json"

    def test_wrong_content_type(self, world: World) -> None:
        assert (
            world.post(push(), content_type="application/x-www-form-urlencoded").status_code == 415
        )
        assert world.post(push(), content_type="text/plain").status_code == 415

    @pytest.mark.parametrize(
        "delivery",
        [
            None,
            "",
            "not-a-uuid",
            DELIVERY.upper(),
            "0a1b2c3d-0000-4000-8000-00000000000",
        ],
    )
    def test_delivery_id_must_be_a_canonical_uuid(self, world: World, delivery: str | None) -> None:
        assert world.post(push(), delivery=delivery).status_code == 400
        assert world.scans == []

    def test_unknown_events_are_accepted_but_neither_parsed_nor_stored(self, world: World) -> None:
        response = world.post(None, raw=b"\xff\xfe garbage", event="star")
        assert response.status_code == 202
        assert world.h.store.state.deliveries == {}

    def test_ping(self, world: World) -> None:
        assert world.post({"zen": "x"}, event="ping").status_code == 202
        assert world.scans == []

    @pytest.mark.parametrize(
        "raw",
        [b"{", b'{"a":1,"a":2}', b'{"after":NaN}', b"[]", b"null", b'"x"'],
    )
    def test_malformed_json_is_400(self, world: World, raw: bytes) -> None:
        assert world.post(None, raw=raw).status_code == 400

    def test_duplicate_keys_cannot_smuggle_a_second_value(self, world: World) -> None:
        raw = json.dumps(push()).encode()[:-1] + b',"after":"' + b"b" * 40 + b'"}'
        assert world.post(None, raw=raw).status_code == 400
        assert world.scans == []

    @pytest.mark.parametrize(
        "mutation",
        [
            {"after": "A" * 40},
            {"after": "a" * 39},
            {"after": 5},
            {"ref": ""},
            {"ref": "-main"},
            {"ref": "refs/heads/a..b"},
            {"installation": {"id": "777"}},
            {"installation": {"id": 0}},
            {"installation": {"id": True}},
            {"installation": None},
            {"repository": {"id": -1}},
            {"repository": {}},
        ],
    )
    def test_schema_violations_are_400_and_change_nothing(
        self, world: World, mutation: dict[str, Any]
    ) -> None:
        response = world.post(push(**mutation))
        assert response.status_code == 400
        assert world.scans == []
        assert world.h.store.state.deliveries == {}

    def test_validation_errors_do_not_echo_input(self, world: World) -> None:
        response = world.post(push(after="topsecret-value"))
        assert b"topsecret" not in response.data


class TestIdempotencyAndOutcomes:
    def test_redelivery_is_accepted_and_creates_nothing(self, world: World) -> None:
        assert world.post(push()).status_code == 202
        assert world.post(push()).status_code == 202
        assert len(world.scans) == 1
        assert len(world.h.queue.enqueued) == 1

    def test_unknown_repository_is_accepted_silently(self, world: World) -> None:
        response = world.post(push(repository={"id": 999_999}))
        assert response.status_code == 202
        assert world.scans == []

    def test_installation_mismatch_is_accepted_silently_and_audited(self, world: World) -> None:
        response = world.post(push(installation={"id": INSTALLATION_ID + 1}))
        assert response.status_code == 202
        assert world.scans == []
        assert [e.action for e in world.h.store.state.audit] == ["webhook.rejected"]

    def test_untrusted_urls_and_names_are_never_used(self, world: World) -> None:
        world.post(push())
        (scan,) = world.scans
        repository = world.h.store.state.repositories[scan.repository_id]
        assert repository.ref.full_name == "acme/api"

    def test_branch_deletion_and_tags_are_ignored(self, world: World) -> None:
        assert world.post(push(after="0" * 40), delivery=DELIVERY).status_code == 202
        other = "0a1b2c3d-0000-4000-8000-000000000002"
        assert world.post(push(ref="refs/tags/v1"), delivery=other).status_code == 202
        assert world.scans == []

    def test_broker_down_after_persisting_is_still_202(self, world: World) -> None:
        def broken(*_a: object, **_k: object) -> None:
            raise QueueUnavailableError("down")

        world.h.queue.enqueue = broken  # type: ignore[method-assign]
        assert world.post(push()).status_code == 202
        (scan,) = world.scans
        assert scan.status is ScanStatus.QUEUED

    def test_database_down_is_503_with_retry_after(self, world: World) -> None:
        def broken() -> None:
            raise PersistenceUnavailableError("db down")

        world.h.store.uow = broken  # type: ignore[method-assign,assignment]

        # `HandleGithubEvent` ya capturó la fábrica original; se simula con el caso de uso.
        class Boom:
            def execute(self, _command: object) -> None:
                raise PersistenceUnavailableError("db down")

        world.app.view_functions  # noqa: B018 - la app ya está construida
        webhook = WebhookDependencies(
            verifier=WebhookVerifier([SECRET]),
            handle_event=Boom(),  # type: ignore[arg-type]
            sync_queue=world.sync,
        )
        app = create_app(api_deps(webhook)[0])
        body = json.dumps(push()).encode()
        signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        response = app.test_client().post(
            URL,
            data=body,
            content_type="application/json",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": DELIVERY,
                "X-Hub-Signature-256": signature,
            },
        )
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "30"
        assert response.mimetype == "application/problem+json"


class TestPullRequests:
    def test_scans_the_github_pull_ref(self, world: World) -> None:
        assert world.post(pull_request(), event="pull_request").status_code == 202
        (scan,) = world.scans
        assert scan.ref.value == "refs/pull/7/head"
        assert scan.pull_request_number == 7

    def test_irrelevant_actions_are_ignored(self, world: World) -> None:
        world.post(pull_request(action="closed"), event="pull_request")
        world.post(
            pull_request(action="labeled"), event="pull_request", delivery=DELIVERY[:-1] + "2"
        )
        assert world.scans == []

    def test_fork_prs_follow_the_policy(self) -> None:
        fork = pull_request()
        fork["pull_request"]["head"]["repo"] = {"id": 5555}
        allowed = World(scan_forks=True)
        allowed.post(fork, event="pull_request")
        assert len(allowed.scans) == 1
        refused = World(scan_forks=False)
        refused.post(fork, event="pull_request")
        assert refused.scans == []

    def test_deleted_fork_counts_as_fork(self) -> None:
        deleted = pull_request()
        deleted["pull_request"]["head"]["repo"] = None
        refused = World(scan_forks=False)
        refused.post(deleted, event="pull_request")
        assert refused.scans == []

    def test_pull_request_number_is_bounded(self, world: World) -> None:
        assert world.post(pull_request(number=0), event="pull_request").status_code == 400
        assert world.post(pull_request(number="7"), event="pull_request").status_code == 400


class TestInstallationEvents:
    @pytest.mark.parametrize("event", ["installation", "installation_repositories", "repository"])
    def test_only_the_installation_id_is_queued(self, world: World, event: str) -> None:
        payload = {
            "action": "created",
            "installation": {"id": 31, "account": {"login": "evil"}},
            "repositories_added": [{"id": 1, "full_name": "evil/x"}],
        }
        assert world.post(payload, event=event).status_code == 202
        assert world.sync.queued == [31]
        assert world.scans == []

    def test_missing_installation_is_400(self, world: World) -> None:
        assert world.post({"action": "created"}, event="installation").status_code == 400
        assert world.sync.queued == []

    def test_unsigned_installation_events_do_nothing(self, world: World) -> None:
        world.post({"installation": {"id": 31}}, event="installation", signature=None)
        assert world.sync.queued == []

    def test_broker_down_is_503_so_github_marks_it_failed(self, world: World) -> None:
        def broken(_id: int) -> None:
            raise QueueUnavailableError("down")

        world.sync.enqueue_sync = broken  # type: ignore[method-assign]
        response = world.post({"installation": {"id": 31}}, event="installation")
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "30"


def test_delivery_ids_are_valid_uuids() -> None:
    assert str(UUID(DELIVERY)) == DELIVERY


class RecordedEvents:
    def __init__(self) -> None:
        self.reasons: list[WebhookReason] = []

    def execute(self, reason: WebhookReason) -> None:
        self.reasons.append(reason)


def test_rejections_and_duplicates_are_counted_without_touching_deliveries() -> None:
    """ADR 0030: se cuentan por motivo; el rechazo por firma sigue sin escribir entregas."""
    h = make_harness()
    recorded = RecordedEvents()
    webhook = WebhookDependencies(
        verifier=WebhookVerifier([SECRET]),
        handle_event=h.webhook,
        sync_queue=FakeSyncQueue(),
        record_event=recorded,
    )
    client = create_app(api_deps(webhook, harness=h)[0]).test_client()
    body = json.dumps(push()).encode()
    good = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": "push", "X-GitHub-Delivery": DELIVERY}
    client.post(URL, data=body, headers={**headers, "X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert h.store.state.deliveries == {}
    client.post(
        URL, data=body, headers={**headers, "X-Hub-Signature-256": good}, content_type="text/plain"
    )
    client.post(
        URL,
        data=body,
        headers={**headers, "X-GitHub-Delivery": "x", "X-Hub-Signature-256": good},
        content_type="application/json",
    )
    for _ in range(2):
        client.post(
            URL,
            data=body,
            headers={**headers, "X-Hub-Signature-256": good},
            content_type="application/json",
        )
    assert recorded.reasons == [
        WebhookReason.SIGNATURE,
        WebhookReason.MEDIA_TYPE,
        WebhookReason.MALFORMED,
        WebhookReason.DUPLICATE,
    ]
