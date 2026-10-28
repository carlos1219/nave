"""Celery contra un Redis real: formato de los mensajes, tope de cola, consumo por un worker
de verdad, reintentos y rechazo de mensajes envenenados (AM-303, AM-304, AM-307)."""

import base64
import json
import os
import pickle
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import redis
from celery import Celery
from celery.beat import Scheduler
from celery.contrib.testing.worker import start_worker

from nave.application.errors import FetchUnavailableError, QueueUnavailableError
from nave.application.ports.messaging import ScanStep
from nave.application.use_cases.execute_scan import ExecuteScanOutcome
from nave.application.use_cases.report_to_vcs import ReportOutcome
from nave.delivery.tasks.maintenance_tasks import (
    MaintenanceTaskDependencies,
    register_maintenance_tasks,
)
from nave.delivery.tasks.scan_tasks import ScanTaskDependencies, register_scan_tasks
from nave.domain.value_objects.identifiers import ScanId
from nave.infrastructure.queue.celery_app import create_celery_app, create_scheduler_app
from nave.infrastructure.queue.celery_queue_adapter import CeleryScanQueue
from nave.infrastructure.queue.names import TASK_RELAY_OUTBOX
from nave.infrastructure.queue.retry import RetryPolicy
from nave.infrastructure.queue.settings import CelerySettings, TaskTimeLimits

pytestmark = pytest.mark.integration

SCAN = ScanId(uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"))


class Recorder:
    def __init__(self, *steps: object) -> None:
        self.steps = list(steps)
        self.commands: list[object] = []
        self.done = threading.Event()

    def execute(self, command: object) -> object:
        self.commands.append(command)
        step = self.steps.pop(0) if self.steps else ExecuteScanOutcome.COMPLETED
        if isinstance(step, BaseException):
            raise step
        if not self.steps:
            self.done.set()
        return step


def make_settings(url: str, depth: int = 1000) -> CelerySettings:
    return CelerySettings(
        broker_url=url,
        scan=TaskTimeLimits(timedelta(minutes=25), timedelta(minutes=26)),
        retry=RetryPolicy(
            max_retries=3, base=timedelta(milliseconds=40), cap=timedelta(milliseconds=200)
        ),
        max_queue_depth=depth,
    )


def make_app(url: str, execute: Recorder, report: Recorder | None = None) -> Celery:
    settings = make_settings(url)
    app = create_celery_app(settings)
    register_scan_tasks(
        app,
        ScanTaskDependencies(
            execute_scan=execute,  # type: ignore[arg-type]
            report_to_vcs=report or Recorder(ReportOutcome.REPORTED),  # type: ignore[arg-type]
            abandon_scan=Recorder(True),  # type: ignore[arg-type]
            retry=settings.retry,
        ),
        scan_limits=settings.scan,
        report_limits=settings.report,
    )
    return app


@pytest.fixture
def worker_for() -> Iterator[Callable[[Celery], Any]]:
    stack: list[Any] = []

    def start(app: Celery) -> Any:
        ctx = start_worker(app, pool="solo", perform_ping_check=False, loglevel="WARNING")
        stack.append(ctx)
        return ctx.__enter__()

    yield start
    for ctx in reversed(stack):
        ctx.__exit__(None, None, None)


def raw(url: str) -> redis.Redis:
    return redis.Redis.from_url(url)


def test_the_message_in_redis_carries_only_the_scan_id(clean_redis: str) -> None:
    app = create_celery_app(make_settings(clean_redis))
    CeleryScanQueue(app, max_queue_depth=10).enqueue(ScanStep.EXECUTE, SCAN)
    (stored,) = raw(clean_redis).lrange("scan", 0, -1)
    message = json.loads(stored)
    args, kwargs, _embed = json.loads(base64.b64decode(message["body"]))
    assert (args, kwargs) == ([str(SCAN)], {})
    assert message["headers"]["id"] == f"{SCAN}:EXECUTE"
    assert message["headers"]["task"] == "nave.scan.execute"
    assert message["content-type"] == "application/json"


def test_report_goes_to_its_own_queue(clean_redis: str) -> None:
    app = create_celery_app(make_settings(clean_redis))
    CeleryScanQueue(app, max_queue_depth=10).enqueue(ScanStep.REPORT, SCAN)
    client = raw(clean_redis)
    assert (client.llen("report"), client.llen("scan")) == (1, 0)


def test_a_full_queue_refuses_work_until_it_drains(clean_redis: str) -> None:
    app = create_celery_app(make_settings(clean_redis))
    queue = CeleryScanQueue(app, max_queue_depth=2)
    for n in range(2):
        queue.enqueue(ScanStep.EXECUTE, ScanId(uuid.UUID(int=n + 1)))
    with pytest.raises(QueueUnavailableError):
        queue.enqueue(ScanStep.EXECUTE, SCAN)
    assert raw(clean_redis).llen("scan") == 2
    raw(clean_redis).lpop("scan")
    queue.enqueue(ScanStep.EXECUTE, SCAN)


def test_an_unreachable_broker_is_a_transient_error_not_a_crash() -> None:
    app = create_celery_app(make_settings("redis://default:x@127.0.0.1:1/0"))
    with pytest.raises(QueueUnavailableError):
        CeleryScanQueue(app, max_queue_depth=10).enqueue(ScanStep.EXECUTE, SCAN)


def test_a_wrong_redis_password_is_rejected_by_the_broker(clean_redis: str) -> None:
    bad = clean_redis.replace("it-redis-pw", "wrong")
    app = create_celery_app(make_settings(bad))
    with pytest.raises(QueueUnavailableError):
        CeleryScanQueue(app, max_queue_depth=10).enqueue(ScanStep.EXECUTE, SCAN)


def test_a_real_worker_runs_the_use_case_for_an_enqueued_scan(
    clean_redis: str, worker_for: Callable[[Celery], Any]
) -> None:
    execute, report = Recorder(), Recorder(ReportOutcome.REPORTED)
    app = make_app(clean_redis, execute, report)
    worker_for(app)
    queue = CeleryScanQueue(app, max_queue_depth=10)
    queue.enqueue(ScanStep.EXECUTE, SCAN)
    queue.enqueue(ScanStep.REPORT, SCAN)
    assert execute.done.wait(15)
    assert report.done.wait(15)
    assert [type(c).__name__ for c in execute.commands] == ["ExecuteScanCommand"]
    assert getattr(execute.commands[0], "scan_id") == SCAN  # noqa: B009


def test_a_transient_failure_is_retried_through_the_real_broker(
    clean_redis: str, worker_for: Callable[[Celery], Any]
) -> None:
    execute = Recorder(
        FetchUnavailableError(), FetchUnavailableError(), ExecuteScanOutcome.COMPLETED
    )
    app = make_app(clean_redis, execute)
    worker_for(app)
    CeleryScanQueue(app, max_queue_depth=10).enqueue(ScanStep.EXECUTE, SCAN)
    assert execute.done.wait(20)
    assert len(execute.commands) == 3


def test_a_pickled_message_written_straight_into_redis_is_never_deserialized(
    clean_redis: str, worker_for: Callable[[Celery], Any], tmp_path: Path
) -> None:
    class Canary:
        def __reduce__(self) -> tuple[Callable[..., Any], tuple[str]]:
            return (os.mkdir, (str(tmp_path / "pwned"),))

    execute = Recorder()
    app = make_app(clean_redis, execute)
    worker_for(app)
    body = base64.b64encode(pickle.dumps(((Canary(),), {}, {})))
    message = {
        "body": body.decode(),
        "content-encoding": "binary",
        "content-type": "application/x-python-serialize",
        "headers": {
            "lang": "py",
            "task": "nave.scan.execute",
            "id": str(uuid.uuid4()),
            "root_id": str(uuid.uuid4()),
            "retries": 0,
            "argsrepr": "()",
            "kwargsrepr": "{}",
        },
        "properties": {
            "correlation_id": str(uuid.uuid4()),
            "reply_to": "x",
            "delivery_mode": 2,
            "delivery_info": {"exchange": "nave", "routing_key": "scan"},
            "priority": 0,
            "body_encoding": "base64",
            "delivery_tag": str(uuid.uuid4()),
        },
    }
    client = raw(clean_redis)
    client.lpush("scan", json.dumps(message))
    deadline = time.monotonic() + 10
    while client.llen("scan") and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)
    assert client.llen("scan") == 0
    assert not (tmp_path / "pwned").exists()
    assert execute.commands == []


def test_a_message_for_an_unknown_task_name_runs_nothing(
    clean_redis: str, worker_for: Callable[[Celery], Any]
) -> None:
    execute = Recorder()
    app = make_app(clean_redis, execute)
    worker_for(app)
    app.send_task("os.system", args=["echo pwned"], queue="scan")
    time.sleep(1.0)
    assert execute.commands == []


class CountingRelay:
    def __init__(self) -> None:
        self.runs = threading.Event()

    def execute(self) -> int:
        self.runs.set()
        return 0


def test_beat_publishes_due_tasks_to_the_maintenance_queue_with_an_expiry(
    clean_redis: str,
) -> None:
    app = create_scheduler_app(make_settings(clean_redis))
    scheduler = Scheduler(app=app)
    for entry in scheduler.schedule.values():
        entry.last_run_at -= timedelta(days=2)
    for _ in range(6):  # cada tick atiende como mucho una entrada vencida
        scheduler.tick()
    client = raw(clean_redis)
    assert (client.llen("maintenance"), client.llen("scan"), client.llen("report")) == (3, 0, 0)
    headers = [json.loads(m)["headers"] for m in client.lrange("maintenance", 0, -1)]
    assert {h["task"] for h in headers} == {
        "nave.maintenance.relay_outbox",
        "nave.maintenance.sweep_scans",
        "nave.maintenance.purge_expired_data",
    }
    assert all(h["expires"] for h in headers)


def test_a_real_worker_runs_a_scheduled_maintenance_task(
    clean_redis: str, worker_for: Callable[[Celery], Any]
) -> None:
    relay = CountingRelay()
    app = create_celery_app(make_settings(clean_redis))
    register_maintenance_tasks(
        app,
        MaintenanceTaskDependencies(
            relay_outbox=relay,  # type: ignore[arg-type]
            sweep_scans=Recorder(),  # type: ignore[arg-type]
            purge_expired_data=Recorder(),  # type: ignore[arg-type]
        ),
        limits=make_settings(clean_redis).maintenance,
    )
    worker_for(app)
    scheduler = create_scheduler_app(make_settings(clean_redis))
    scheduler.send_task(TASK_RELAY_OUTBOX, queue="maintenance")
    assert relay.runs.wait(15)
