from dataclasses import replace
from datetime import timedelta

import pytest

from nave.bootstrap.config import ConfigurationError, Settings
from nave.bootstrap.container import ExternalAdapters, build_container
from nave.infrastructure.queue.schedule import build_beat_schedule
from tests.fakes.installations import FakeDirectory
from tests.fakes.memory import FakeFetcher, FakeRegistry, FakeScanner, RecordingVcs


def settings(**env: str) -> Settings:
    environ = {
        "NAVE_DATABASE_URL": "postgresql+psycopg://nave_app@postgres:5432/nave",
        "NAVE_DATABASE_PASSWORD_FILE": "/run/secrets/db",
        "NAVE_REDIS_URL": "redis://redis:6379/0",
        "NAVE_REDIS_PASSWORD_FILE": "/run/secrets/redis",
        "NAVE_FINGERPRINT_KEY_FILE": "/run/secrets/fingerprint",
        "NAVE_MAINTENANCE_DATABASE_URL": "postgresql+psycopg://nave_maintenance@postgres:5432/nave",
        "NAVE_MAINTENANCE_DATABASE_PASSWORD_FILE": "/run/secrets/maintenance",
        **env,
    }
    return Settings.from_environ(
        environ, read_secret=lambda path: "ab" * 32 if path.name == "fingerprint" else "secret"
    )


def adapters(scanners: int = 2) -> ExternalAdapters:
    kinds = ["semgrep", "trivy", "gitleaks"][:scanners]
    return ExternalAdapters(
        fetcher=FakeFetcher(),
        registry=FakeRegistry(*(FakeScanner(k, RuntimeError()) for k in kinds)),
        vcs=RecordingVcs(),
        directory=FakeDirectory(),
    )


def test_the_worker_container_registers_the_scan_tasks_on_its_own_app() -> None:
    container = build_container(settings(), adapters())
    try:
        assert {
            "nave.scan.execute",
            "nave.scan.report",
            "nave.maintenance.relay_outbox",
            "nave.maintenance.sweep_scans",
            "nave.maintenance.purge_expired_data",
            "nave.maintenance.purge_organizations",
            "nave.maintenance.scrub_orphaned_users",
            "nave.installations.sync",
            "nave.installations.reconcile",
        } <= set(container.celery.tasks)
        assert container.celery.tasks["nave.scan.execute"].soft_time_limit == (
            container.settings.celery.scan.soft.total_seconds()
        )
    finally:
        container.close()


def test_starting_with_a_time_budget_smaller_than_the_scanners_need_is_refused() -> None:
    # 300 fetch + 3 * 600 scan no cabe en el límite por defecto de 2 escáneres (1620 s)
    with pytest.raises(ConfigurationError, match="SOFT_SECONDS"):
        build_container(settings(), adapters(scanners=3))


def test_a_larger_budget_allows_more_scanners() -> None:
    s = settings(NAVE_SCAN_TASK_SOFT_SECONDS="3000", NAVE_SCAN_TASK_HARD_SECONDS="3100")
    container = build_container(s, adapters(scanners=3))
    container.close()
    assert s.fetch_limits.max_duration + 3 * s.scan_limits.max_duration < timedelta(seconds=3000)


def test_the_container_does_not_hold_secrets_in_its_repr() -> None:
    container = build_container(settings(), adapters())
    try:
        assert "secret" not in repr(container)
    finally:
        container.close()


def test_every_scheduled_task_exists_in_the_worker() -> None:
    container = build_container(settings(), adapters())
    try:
        for entry in build_beat_schedule().values():
            assert entry["task"] in container.celery.tasks
    finally:
        container.close()


def test_the_worker_needs_the_maintenance_credential() -> None:
    s = settings()
    with pytest.raises(ConfigurationError, match="MAINTENANCE"):
        build_container(replace(s, maintenance_database_url=None), adapters())


def test_the_two_database_roles_use_separate_engines() -> None:
    container = build_container(settings(), adapters())
    try:
        assert container.engine.url.username == "nave_app"
        assert container.maintenance_engine.url.username == "nave_maintenance"
    finally:
        container.close()
