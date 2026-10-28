from datetime import timedelta

import pytest
from celery import Celery

from nave.application.dto.maintenance import OrganizationPurgeReport, RetentionReport, SweepReport
from nave.application.errors import (
    ConcurrentModificationError,
    PersistenceUnavailableError,
    QueueUnavailableError,
)
from nave.delivery.tasks.maintenance_tasks import (
    MaintenanceTaskDependencies,
    register_maintenance_tasks,
    run_purge_expired_data,
    run_purge_organizations,
    run_relay_outbox,
    run_scrub_users,
    run_sweep_scans,
)
from nave.infrastructure.queue.celery_app import create_celery_app
from nave.infrastructure.queue.settings import CelerySettings, TaskTimeLimits


class Stub:
    def __init__(self, result: object | Exception) -> None:
        self.result, self.calls = result, 0

    def execute(self) -> object:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def deps(
    relay: object | Exception = 0,
    sweep: object | Exception | None = None,
    purge: object | Exception | None = None,
    orgs: object | Exception | None = None,
    scrub: object | Exception = 0,
) -> MaintenanceTaskDependencies:
    return MaintenanceTaskDependencies(
        relay_outbox=Stub(relay),  # type: ignore[arg-type]
        sweep_scans=Stub(SweepReport() if sweep is None else sweep),  # type: ignore[arg-type]
        purge_expired_data=Stub(RetentionReport() if purge is None else purge),  # type: ignore[arg-type]
        purge_organizations=Stub(OrganizationPurgeReport() if orgs is None else orgs),  # type: ignore[arg-type]
        scrub_users=Stub(scrub),  # type: ignore[arg-type]
    )


def test_each_task_runs_its_use_case_once() -> None:
    d = deps()
    run_relay_outbox(d)
    run_sweep_scans(d)
    run_purge_expired_data(d)
    run_purge_organizations(d)
    run_scrub_users(d)
    for stub in (
        d.relay_outbox,
        d.sweep_scans,
        d.purge_expired_data,
        d.purge_organizations,
        d.scrub_users,
    ):
        assert stub.calls == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "error", [PersistenceUnavailableError(), QueueUnavailableError(), ConcurrentModificationError()]
)
def test_transient_failures_wait_for_the_next_scheduled_run(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    d = deps(relay=error, sweep=error, purge=error, orgs=error, scrub=error)
    run_relay_outbox(d)
    run_sweep_scans(d)
    run_purge_expired_data(d)
    run_purge_organizations(d)
    run_scrub_users(d)
    assert caplog.text.count("postponed") == 5


def test_unexpected_failures_are_not_swallowed() -> None:
    d = deps(
        relay=ZeroDivisionError(),
        sweep=ZeroDivisionError(),
        purge=ZeroDivisionError(),
        orgs=ZeroDivisionError(),
        scrub=ZeroDivisionError(),
    )
    for run in (
        run_relay_outbox,
        run_sweep_scans,
        run_purge_expired_data,
        run_purge_organizations,
        run_scrub_users,
    ):
        with pytest.raises(ZeroDivisionError):
            run(d)


def test_recovered_scans_are_logged_as_warnings(caplog: pytest.LogCaptureFixture) -> None:
    run_sweep_scans(deps(sweep=SweepReport(requeued=1, expired_in_queue=2, abandoned_running=3)))
    assert "stuck scans recovered" in caplog.text
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_a_quiet_sweep_and_relay_log_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    run_relay_outbox(deps(relay=0))
    run_sweep_scans(deps(sweep=SweepReport()))
    assert caplog.records == []


def eager_app(d: MaintenanceTaskDependencies) -> Celery:
    settings = CelerySettings(
        broker_url="redis://default:pw@localhost:6379/0",
        scan=TaskTimeLimits(timedelta(minutes=25), timedelta(minutes=26)),
    )
    app = create_celery_app(settings)
    app.conf.task_always_eager = True
    register_maintenance_tasks(app, d, limits=settings.maintenance)
    return app


def test_registered_tasks_use_the_maintenance_queue_and_are_bounded() -> None:
    app = eager_app(deps())
    names = {
        "nave.maintenance.relay_outbox",
        "nave.maintenance.sweep_scans",
        "nave.maintenance.purge_expired_data",
        "nave.maintenance.purge_organizations",
        "nave.maintenance.scrub_orphaned_users",
    }
    assert names <= set(app.tasks)
    for name in names:
        task = app.tasks[name]
        assert task.queue == "maintenance"
        assert (task.soft_time_limit, task.time_limit) == (240, 300)
        assert task.acks_late is True
        assert task.ignore_result is True


def test_celery_runs_the_registered_tasks() -> None:
    d = deps(relay=3)
    app = eager_app(d)
    app.tasks["nave.maintenance.relay_outbox"].apply().get()
    app.tasks["nave.maintenance.sweep_scans"].apply().get()
    app.tasks["nave.maintenance.purge_expired_data"].apply().get()
    app.tasks["nave.maintenance.purge_organizations"].apply().get()
    app.tasks["nave.maintenance.scrub_orphaned_users"].apply().get()
    assert d.scrub_users.calls == 1  # type: ignore[attr-defined]
    assert d.relay_outbox.calls == d.sweep_scans.calls == d.purge_expired_data.calls == 1  # type: ignore[attr-defined]
    assert d.purge_organizations.calls == 1  # type: ignore[attr-defined]


def test_a_refused_purge_is_logged_as_an_error(caplog: pytest.LogCaptureFixture) -> None:
    run_purge_organizations(deps(orgs=OrganizationPurgeReport(refused=2)))
    assert any(r.levelname == "ERROR" for r in caplog.records)
