"""AM-304 (deserialización) y reglas de fiabilidad de CLAUDE.md §10 sobre la configuración
real de Celery. Si alguien relaja un valor, falla aquí."""

from datetime import timedelta
from typing import Any

import pytest
from celery import current_app

from nave.infrastructure.queue.celery_app import build_celery_config, create_celery_app
from nave.infrastructure.queue.names import ALL_QUEUES
from nave.infrastructure.queue.settings import CelerySettings, TaskTimeLimits

BROKER = "redis://default:pw@redis:6379/0"


@pytest.fixture
def settings() -> CelerySettings:
    return CelerySettings(
        broker_url=BROKER,
        scan=TaskTimeLimits(timedelta(minutes=25), timedelta(minutes=26)),
    )


@pytest.fixture
def config(settings: CelerySettings) -> dict[str, Any]:
    return build_celery_config(settings)


def test_only_json_is_ever_deserialized(config: dict[str, Any]) -> None:
    assert config["accept_content"] == ["json"]
    for key in ("task_serializer", "result_serializer", "event_serializer"):
        assert config[key] == "json"


def test_the_live_app_rejects_pickle_and_yaml(settings: CelerySettings) -> None:
    app = create_celery_app(settings)
    assert list(app.conf.accept_content) == ["json"]
    assert "pickle" not in str(app.conf.task_serializer)


def test_redis_is_never_a_result_store(config: dict[str, Any]) -> None:
    assert config["result_backend"] is None
    assert config["task_ignore_result"] is True


def test_tasks_are_acknowledged_late_and_survive_a_lost_worker(config: dict[str, Any]) -> None:
    assert config["task_acks_late"] is True
    assert config["task_reject_on_worker_lost"] is True
    assert config["worker_prefetch_multiplier"] == 1
    assert config["worker_cancel_long_running_tasks_on_connection_loss"] is True


def test_every_task_has_a_time_limit_by_default(config: dict[str, Any]) -> None:
    assert 0 < config["task_soft_time_limit"] < config["task_time_limit"]


def test_queues_are_closed_and_exactly_the_documented_ones(config: dict[str, Any]) -> None:
    assert config["task_create_missing_queues"] is False
    assert sorted(q.name for q in config["task_queues"]) == sorted(ALL_QUEUES)


def test_visibility_timeout_prevents_double_delivery(
    config: dict[str, Any], settings: CelerySettings
) -> None:
    visibility = config["broker_transport_options"]["visibility_timeout"]
    assert (
        visibility > settings.scan.hard.total_seconds() + settings.retry.max_delay.total_seconds()
    )


def test_broker_calls_cannot_hang_forever(config: dict[str, Any]) -> None:
    options = config["broker_transport_options"]
    assert options["socket_timeout"] > 0
    assert options["socket_connect_timeout"] > 0
    assert config["task_publish_retry_policy"]["max_retries"] <= 3


def test_no_task_events_leak_scan_ids_to_the_broker(config: dict[str, Any]) -> None:
    assert config["worker_send_task_events"] is False
    assert config["task_send_sent_event"] is False


def test_the_app_is_not_installed_as_the_global_current_app(settings: CelerySettings) -> None:
    app = create_celery_app(settings)
    assert current_app is not app
    assert app.main == "nave"


def test_the_broker_password_is_not_in_the_app_repr(settings: CelerySettings) -> None:
    assert "pw@" not in repr(create_celery_app(settings))
