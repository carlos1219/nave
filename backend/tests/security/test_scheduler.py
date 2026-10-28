"""ADR 0008: el scheduler solo puede encolar tareas de mantenimiento, con la planificación
fijada en código y sin credenciales de base de datos ni de GitHub."""

from collections.abc import Mapping
from pathlib import Path

import pytest
from celery.schedules import crontab

from nave.bootstrap.config import ConfigurationError, Settings
from nave.bootstrap.scheduler import build_scheduler, scheduler_app
from nave.infrastructure.queue.celery_app import create_scheduler_app
from nave.infrastructure.queue.names import MAINTENANCE_QUEUE
from nave.infrastructure.queue.schedule import build_beat_schedule

REDIS_ONLY = {
    "NAVE_REDIS_URL": "redis://redis:6379/0",
    "NAVE_REDIS_PASSWORD_FILE": "/run/secrets/redis_password",
}


class SecretReader:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, path: Path) -> str:
        self.paths.append(str(path))
        return "redis-pw"


def test_every_periodic_task_goes_to_the_maintenance_queue_and_expires() -> None:
    schedule = build_beat_schedule()
    assert set(schedule) == {
        "relay-outbox",
        "sweep-stuck-scans",
        "purge-expired-data",
        "reconcile-installations",
        "purge-uninstalled-organizations",
        "scrub-orphaned-users",
        "health-check",
    }
    for entry in schedule.values():
        assert entry["options"]["queue"] == MAINTENANCE_QUEUE
        assert 0 < entry["options"]["expires"] <= 6 * 3600
        assert entry["task"].startswith(("nave.maintenance.", "nave.installations."))


def test_the_purge_runs_once_a_day_at_a_fixed_off_peak_minute() -> None:
    purge = build_beat_schedule()["purge-expired-data"]["schedule"]
    assert isinstance(purge, crontab)
    assert (purge.hour, purge.minute) == ({3}, {27})
    assert purge.day_of_week == set(range(7))


def test_the_scheduler_needs_only_the_redis_secret() -> None:
    reader = SecretReader()
    settings = Settings.celery_only(REDIS_ONLY, read_secret=reader)
    assert reader.paths == ["/run/secrets/redis_password"]
    assert settings.broker_url.startswith("redis://default:redis-pw@redis")


def test_the_scheduler_app_registers_no_tasks_and_keeps_no_state_file() -> None:
    settings = Settings.celery_only(REDIS_ONLY, read_secret=SecretReader())
    app = create_scheduler_app(settings)
    app.finalize()
    assert not [name for name in app.tasks if name.startswith("nave.")]
    assert app.conf.beat_scheduler == "celery.beat:Scheduler"
    assert set(app.conf.beat_schedule) == set(build_beat_schedule())
    assert app.conf.result_backend is None


@pytest.mark.usefixtures("restore_root_logger")
def test_the_scheduler_entrypoint_builds_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "redis_password"
    secret.write_text("pw\n")
    env: Mapping[str, str] = {**REDIS_ONLY, "NAVE_REDIS_PASSWORD_FILE": str(secret)}
    app = build_scheduler(env)
    assert app.conf.broker_url == "redis://default:pw@redis:6379/0"
    # el entorno real del test puede traer NAVE_TEST_*; la fábrica lo rechazaría a propósito
    monkeypatch.delenv("NAVE_TEST_DATABASE_URL", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert scheduler_app().conf.broker_url == app.conf.broker_url


def test_a_scheduler_given_unknown_settings_refuses_to_start() -> None:
    with pytest.raises(ConfigurationError):
        Settings.celery_only({**REDIS_ONLY, "NAVE_BEAT_SCHEDULE": "x"}, read_secret=SecretReader())
