from contextlib import contextmanager
from typing import Any
from uuid import UUID

import pytest
from kombu.exceptions import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError

from nave.application.errors import QueueUnavailableError
from nave.application.ports.messaging import ScanStep
from nave.domain.value_objects.identifiers import ScanId
from nave.infrastructure.queue.celery_queue_adapter import (
    CeleryInstallationSyncQueue,
    CeleryScanQueue,
)
from nave.infrastructure.queue.names import task_id_for

SCAN = ScanId(UUID("11111111-1111-4111-8111-111111111111"))


class FakeClient:
    def __init__(self, depth: Any = 0, error: Exception | None = None) -> None:
        self.depth, self.error = depth, error

    def llen(self, queue: str) -> Any:
        if self.error:
            raise self.error
        return self.depth


class FakeControl:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.revoked = error, []

    def revoke(self, ids: list[str], *, terminate: bool) -> None:
        if self.error:
            raise self.error
        self.revoked.append((ids, terminate))


class FakeApp:
    def __init__(self, client: FakeClient | None = None, send_error: Exception | None = None):
        self.client = client or FakeClient()
        self.send_error = send_error
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.control = FakeControl()

    @contextmanager
    def connection_for_write(self):  # type: ignore[no-untyped-def]
        class Channel:
            client = self.client

        class Connection:
            default_channel = Channel()

        yield Connection()

    def send_task(self, name: str, **kwargs: Any) -> None:
        if self.send_error:
            raise self.send_error
        self.sent.append((name, kwargs))


def queue(app: FakeApp, depth: int = 10) -> CeleryScanQueue:
    return CeleryScanQueue(app, max_queue_depth=depth)  # type: ignore[arg-type]


def test_enqueue_sends_only_the_scan_id_with_a_deterministic_task_id() -> None:
    app = FakeApp()
    queue(app).enqueue(ScanStep.EXECUTE, SCAN)
    queue(app).enqueue(ScanStep.REPORT, SCAN)
    (n1, k1), (n2, k2) = app.sent
    assert (n1, k1["queue"], k1["args"]) == ("nave.scan.execute", "scan", [str(SCAN)])
    assert (n2, k2["queue"]) == ("nave.scan.report", "report")
    assert k1["task_id"] == task_id_for(ScanStep.EXECUTE, SCAN) == f"{SCAN}:EXECUTE"
    assert k1["serializer"] == "json"
    assert set(k1) == {"args", "task_id", "queue", "serializer"}


@pytest.mark.parametrize("depth", [10, 11, 10_000])
def test_a_full_queue_refuses_new_work(depth: int) -> None:
    app = FakeApp(FakeClient(depth=depth))
    with pytest.raises(QueueUnavailableError):
        queue(app, depth=10).enqueue(ScanStep.EXECUTE, SCAN)
    assert app.sent == []


def test_one_below_the_limit_is_accepted() -> None:
    app = FakeApp(FakeClient(depth=9))
    queue(app, depth=10).enqueue(ScanStep.EXECUTE, SCAN)
    assert len(app.sent) == 1


def test_garbage_from_the_broker_is_not_treated_as_an_empty_queue() -> None:
    app = FakeApp(FakeClient(depth="0"))
    with pytest.raises(QueueUnavailableError):
        queue(app).enqueue(ScanStep.EXECUTE, SCAN)


@pytest.mark.parametrize(
    "error", [OperationalError("down"), RedisConnectionError("down"), ConnectionResetError()]
)
def test_broker_errors_become_a_transient_error_without_leaking_details(error: Exception) -> None:
    for app in (FakeApp(send_error=error), FakeApp(FakeClient(error=error))):
        with pytest.raises(QueueUnavailableError) as raised:
            queue(app).enqueue(ScanStep.EXECUTE, SCAN)
        assert "down" not in str(raised.value)
        assert raised.value.__cause__ is error


def test_programming_errors_are_not_disguised_as_broker_outages() -> None:
    with pytest.raises(ZeroDivisionError):
        queue(FakeApp(send_error=ZeroDivisionError())).enqueue(ScanStep.EXECUTE, SCAN)


def test_revoke_never_terminates_a_running_scan() -> None:
    app = FakeApp()
    queue(app).revoke(SCAN)
    ids, terminate = app.control.revoked[0]
    assert terminate is False
    assert sorted(ids) == sorted([f"{SCAN}:EXECUTE", f"{SCAN}:REPORT"])


def test_revoke_survives_a_broker_outage(caplog: pytest.LogCaptureFixture) -> None:
    app = FakeApp()
    app.control = FakeControl(OperationalError("down"))
    with caplog.at_level("WARNING"):
        queue(app).revoke(SCAN)
    assert "revoke failed" in caplog.text


class TestInstallationSyncQueue:
    def make(self, app: FakeApp, depth: int = 10) -> CeleryInstallationSyncQueue:
        return CeleryInstallationSyncQueue(app, max_queue_depth=depth)  # type: ignore[arg-type]

    def test_sends_only_the_installation_id(self) -> None:
        app = FakeApp()
        self.make(app).enqueue_sync(42)
        ((name, kwargs),) = app.sent
        assert name == "nave.installations.sync"
        assert kwargs == {"args": [42], "queue": "maintenance", "serializer": "json"}

    def test_full_queue_and_broker_errors_are_translated(self) -> None:
        with pytest.raises(QueueUnavailableError):
            self.make(FakeApp(FakeClient(depth=10))).enqueue_sync(1)
        with pytest.raises(QueueUnavailableError):
            self.make(FakeApp(send_error=OperationalError("x"))).enqueue_sync(1)
        with pytest.raises(QueueUnavailableError):
            self.make(FakeApp(FakeClient(error=RedisConnectionError("x")))).enqueue_sync(1)
