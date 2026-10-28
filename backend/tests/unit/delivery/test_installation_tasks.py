from dataclasses import dataclass, field
from datetime import timedelta
from typing import NoReturn

import pytest

from nave.application.dto.installations import SyncOutcome
from nave.application.errors import TransientError, VcsRejectedError
from nave.delivery.tasks.installation_tasks import (
    InstallationTaskDependencies,
    run_reconcile_installations,
    run_sync_installation,
)
from nave.infrastructure.queue.retry import RetryPolicy


class RetryRequested(Exception):  # noqa: N818 - doble de test
    def __init__(self, delay: timedelta) -> None:
        super().__init__("retry")
        self.delay = delay


@dataclass
class Ctx:
    retries: int = 0

    def retry(self, delay: timedelta, error: Exception) -> NoReturn:
        raise RetryRequested(delay)


@dataclass
class Script:
    steps: list[object] = field(default_factory=list)
    calls: list[object] = field(default_factory=list)

    def execute(self, command: object = None) -> object:
        self.calls.append(command)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def deps(sync: Script, reconcile: Script | None = None) -> InstallationTaskDependencies:
    return InstallationTaskDependencies(
        sync_installation=sync,  # type: ignore[arg-type]
        reconcile_installations=reconcile or Script(),  # type: ignore[arg-type]
        retry=RetryPolicy(max_retries=2, uniform01=lambda: 0.0),
    )


@pytest.mark.parametrize("raw", ["10", 0, -1, True, None, 2**63, 1.5, [1]])
def test_malformed_messages_are_discarded(raw: object) -> None:
    sync = Script()
    run_sync_installation(deps(sync), Ctx(), raw)
    assert sync.calls == []


def test_syncs_the_installation() -> None:
    sync = Script([SyncOutcome.REGISTERED])
    run_sync_installation(deps(sync), Ctx(), 10)
    assert len(sync.calls) == 1


def test_transient_failure_retries_with_backoff() -> None:
    sync = Script([TransientError("down")])
    with pytest.raises(RetryRequested) as raised:
        run_sync_installation(deps(sync), Ctx(retries=0), 10)
    assert raised.value.delay > timedelta(0)


def test_retries_are_bounded() -> None:
    sync = Script([TransientError("down")])
    run_sync_installation(deps(sync), Ctx(retries=2), 10)


def test_definitive_rejection_is_not_retried() -> None:
    sync = Script([VcsRejectedError("bad", status=403)])
    run_sync_installation(deps(sync), Ctx(), 10)


def test_reconcile_swallows_transient_and_rejected_errors() -> None:
    for error in (TransientError("x"), VcsRejectedError("y", status=401)):
        run_reconcile_installations(deps(Script(), Script([error])))
    run_reconcile_installations(deps(Script(), Script([3])))
