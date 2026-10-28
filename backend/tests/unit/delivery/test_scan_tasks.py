from dataclasses import dataclass, field
from datetime import timedelta
from typing import NoReturn
from uuid import UUID

import pytest
from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from nave.application.errors import (
    ConcurrentModificationError,
    FetchUnavailableError,
    NotFoundError,
    PermanentScanError,
)
from nave.application.use_cases.abandon_scan import AbandonScanCommand
from nave.application.use_cases.execute_scan import ExecuteScanCommand, ExecuteScanOutcome
from nave.application.use_cases.report_to_vcs import ReportOutcome, ReportToVcsCommand
from nave.delivery.tasks.scan_tasks import (
    ScanTaskDependencies,
    register_scan_tasks,
    run_execute_scan,
    run_report_scan,
)
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.status_reason import ReasonCode
from nave.infrastructure.queue.celery_app import create_celery_app
from nave.infrastructure.queue.retry import RetryPolicy
from nave.infrastructure.queue.settings import CelerySettings, TaskTimeLimits

RAW = "abcdef01-2345-4678-89ab-cdef01234567"
SCAN = ScanId(UUID(RAW))


class RetryRequested(Exception):  # noqa: N818 - doble de test
    def __init__(self, delay: timedelta, error: Exception) -> None:
        super().__init__("retry")
        self.delay, self.error = delay, error


@dataclass
class Ctx:
    retries: int = 0

    def retry(self, delay: timedelta, error: Exception) -> NoReturn:
        raise RetryRequested(delay, error)


@dataclass
class Script:
    """Doble de un caso de uso: devuelve o lanza lo que se le indique en cada llamada."""

    steps: list[object] = field(default_factory=list)
    calls: list[object] = field(default_factory=list)

    def execute(self, command: object) -> object:
        self.calls.append(command)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def deps(
    execute: Script | None = None,
    report: Script | None = None,
    abandon: Script | None = None,
    max_retries: int = 3,
) -> ScanTaskDependencies:
    return ScanTaskDependencies(
        execute_scan=execute or Script(),  # type: ignore[arg-type]
        report_to_vcs=report or Script(),  # type: ignore[arg-type]
        abandon_scan=abandon or Script([True] * 5),  # type: ignore[arg-type]
        retry=RetryPolicy(max_retries=max_retries, uniform01=lambda: 0.0),
    )


def abandoned(script: Script) -> list[AbandonScanCommand]:
    return [c for c in script.calls if isinstance(c, AbandonScanCommand)]


def test_a_finished_scan_is_not_retried_or_abandoned() -> None:
    execute, abandon = Script([ExecuteScanOutcome.COMPLETED]), Script()
    run_execute_scan(deps(execute, abandon=abandon), Ctx(), RAW)
    assert execute.calls == [ExecuteScanCommand(SCAN)]
    assert abandon.calls == []


@pytest.mark.parametrize(
    "raw", [None, 5, b"x", [RAW], "", "not-a-uuid", RAW.upper(), RAW.replace("-", ""), RAW + "\n"]
)
def test_malformed_messages_are_dropped_without_running_anything(
    raw: object, caplog: pytest.LogCaptureFixture
) -> None:
    execute, report = Script(), Script()
    d = deps(execute, report)
    run_execute_scan(d, Ctx(), raw)
    run_report_scan(d, Ctx(), raw)
    assert execute.calls == report.calls == []
    assert "malformed" in caplog.text
    assert "not-a-uuid" not in caplog.text


@pytest.mark.parametrize("error", [FetchUnavailableError(), ConcurrentModificationError()])
def test_transient_errors_ask_celery_for_a_retry_with_backoff(error: Exception) -> None:
    with pytest.raises(RetryRequested) as raised:
        run_execute_scan(deps(Script([error])), Ctx(retries=1), RAW)
    assert raised.value.error is error
    assert raised.value.delay == timedelta(seconds=10)  # base 10s * 2 * 0.5 con jitter 0


def test_the_wait_demanded_by_the_remote_service_is_respected() -> None:
    error = FetchUnavailableError(retry_after=timedelta(minutes=7))
    with pytest.raises(RetryRequested) as raised:
        run_execute_scan(deps(Script([error])), Ctx(), RAW)
    assert raised.value.delay == timedelta(minutes=7)


def test_exhausted_retries_fail_the_scan_instead_of_leaving_it_in_flight() -> None:
    abandon = Script([True])
    run_execute_scan(deps(Script([FetchUnavailableError()]), abandon=abandon), Ctx(retries=3), RAW)
    (command,) = abandoned(abandon)
    assert (command.scan_id, command.reason_code) == (SCAN, ReasonCode.INTERNAL_ERROR)
    assert "fetch_unavailable" in command.detail


def test_the_soft_time_limit_abandons_the_scan_as_a_timeout() -> None:
    abandon = Script([True])
    run_execute_scan(deps(Script([SoftTimeLimitExceeded()]), abandon=abandon), Ctx(), RAW)
    assert abandoned(abandon)[0].reason_code is ReasonCode.TIMEOUT


def test_an_unexpected_error_abandons_the_scan_and_still_surfaces() -> None:
    abandon = Script([True])
    with pytest.raises(ZeroDivisionError):
        run_execute_scan(deps(Script([ZeroDivisionError("boom")]), abandon=abandon), Ctx(), RAW)
    command = abandoned(abandon)[0]
    assert command.reason_code is ReasonCode.INTERNAL_ERROR
    assert command.detail == "ZeroDivisionError"


def test_a_failing_abandon_does_not_hide_the_original_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    abandon = Script([NotFoundError("scan")])
    with pytest.raises(ZeroDivisionError):
        run_execute_scan(deps(Script([ZeroDivisionError()]), abandon=abandon), Ctx(), RAW)
    assert "could not abandon scan" in caplog.text


def test_permanent_errors_that_escape_are_not_retried() -> None:
    abandon = Script([True])
    with pytest.raises(PermanentScanError):
        run_execute_scan(deps(Script([PermanentScanError()]), abandon=abandon), Ctx(), RAW)
    assert len(abandoned(abandon)) == 1


def test_report_runs_the_use_case_once() -> None:
    report = Script([ReportOutcome.REPORTED])
    run_report_scan(deps(report=report), Ctx(), RAW)
    assert report.calls == [ReportToVcsCommand(SCAN)]


def test_report_retries_transient_errors_and_timeouts() -> None:
    for error in (FetchUnavailableError(), SoftTimeLimitExceeded()):
        with pytest.raises(RetryRequested):
            run_report_scan(deps(report=Script([error])), Ctx(), RAW)


def test_report_gives_up_quietly_when_retries_run_out(caplog: pytest.LogCaptureFixture) -> None:
    abandon = Script()
    run_report_scan(
        deps(report=Script([FetchUnavailableError()]), abandon=abandon), Ctx(retries=3), RAW
    )
    assert abandon.calls == []
    assert "report retries exhausted" in caplog.text


def test_report_does_not_retry_programming_errors() -> None:
    with pytest.raises(ZeroDivisionError):
        run_report_scan(deps(report=Script([ZeroDivisionError()])), Ctx(), RAW)


# --- registro en una app real, con ejecución eager (sin broker) -----------------------------


def eager_app(d: ScanTaskDependencies) -> Celery:
    settings = CelerySettings(
        broker_url="redis://default:pw@localhost:6379/0",
        scan=TaskTimeLimits(timedelta(minutes=25), timedelta(minutes=26)),
    )
    app = create_celery_app(settings)
    app.conf.task_always_eager = True
    register_scan_tasks(app, d, scan_limits=settings.scan, report_limits=settings.report)
    return app


def test_registered_tasks_carry_their_queue_limits_and_reliability_flags() -> None:
    app = eager_app(deps())
    execute, report = app.tasks["nave.scan.execute"], app.tasks["nave.scan.report"]
    assert (execute.queue, report.queue) == ("scan", "report")
    assert (execute.soft_time_limit, execute.time_limit) == (1500, 1560)
    assert (report.soft_time_limit, report.time_limit) == (120, 180)
    for task in (execute, report):
        assert task.acks_late is True
        assert task.reject_on_worker_lost is True
        assert task.ignore_result is True


def test_celery_retries_through_the_real_task_until_success() -> None:
    execute = Script(
        [FetchUnavailableError(), ConcurrentModificationError(), ExecuteScanOutcome.COMPLETED]
    )
    app = eager_app(deps(execute, max_retries=5))
    app.tasks["nave.scan.execute"].apply(args=[RAW]).get()
    assert len(execute.calls) == 3


def test_celery_abandons_after_exhausting_retries_through_the_real_task() -> None:
    execute = Script([FetchUnavailableError()] * 3)
    abandon = Script([True])
    app = eager_app(deps(execute, abandon=abandon, max_retries=2))
    app.tasks["nave.scan.execute"].apply(args=[RAW]).get()
    assert len(execute.calls) == 3
    assert len(abandoned(abandon)) == 1


def test_tasks_do_not_leak_into_other_celery_apps_of_the_process() -> None:
    eager_app(deps())
    settings = CelerySettings(
        broker_url="redis://default:pw@localhost:6379/0",
        scan=TaskTimeLimits(timedelta(minutes=25), timedelta(minutes=26)),
    )
    other = create_celery_app(settings)
    other.finalize()
    assert "nave.scan.execute" not in other.tasks
