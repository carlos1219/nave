from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nave.application.dto.audit import AuditEntry
from nave.application.dto.scanning import FetchLimits, ScanLimits, ScannerOutput
from nave.application.dto.vcs import PrCommentBody, VcsTarget
from nave.application.dto.views import FindingQuery, LogLevel, PageRequest, ScanLogEntry
from nave.application.dto.workspace import WorkspaceHandle
from nave.application.errors import ConcurrentModificationError, NotFoundError, RepositoryFetchError
from nave.application.ports.messaging import ScanQueuePort
from nave.application.ports.persistence import UnitOfWork
from nave.application.ports.policy import PolicyEvaluatorPort
from nave.application.ports.scanning import RepositoryFetcherPort, ScannerRegistryPort
from nave.application.ports.system import Clock, IdGenerator
from nave.application.ports.vcs import VcsReporterPort
from nave.domain.errors import InvalidValueError
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.status_reason import ReasonCode
from tests.fakes.memory import (
    DomainPolicyEvaluator,
    FakeClock,
    FakeFetcher,
    FakeRegistry,
    RecordingQueue,
    RecordingVcs,
    SequentialIds,
    Store,
)
from tests.unit.application.builders import DIGEST, OUTPUT_SHA, REPO_ID
from tests.unit.domain.builders import new_queued_scan

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def test_fakes_satisfy_their_ports() -> None:
    store = Store()
    uow: UnitOfWork = store.uow()
    clock: Clock = FakeClock()
    ids: IdGenerator = SequentialIds()
    queue: ScanQueuePort = RecordingQueue()
    fetcher: RepositoryFetcherPort = FakeFetcher()
    registry: ScannerRegistryPort = FakeRegistry()
    evaluator: PolicyEvaluatorPort = DomainPolicyEvaluator()
    vcs: VcsReporterPort = RecordingVcs()
    assert all(x is not None for x in (uow, clock, ids, queue, fetcher, registry, evaluator, vcs))
    assert ids.new_uuid() != ids.new_uuid()


def test_uow_rolls_back_without_commit() -> None:
    store = Store()
    with store.uow() as uow:
        uow.scans.add(new_queued_scan())
    assert store.state.scans == {}
    with store.uow() as uow:
        uow.scans.add(new_queued_scan())
        uow.commit()
    assert len(store.state.scans) == 1


def test_optimistic_locking_detects_lost_updates() -> None:
    store = Store()
    scan = new_queued_scan()
    with store.uow() as uow:
        uow.scans.add(scan)
        uow.commit()
    with store.uow() as uow:
        uow.scans.save(scan)
        uow.commit()
    with store.uow() as uow, pytest.raises(ConcurrentModificationError):
        uow.scans.save(scan)


def test_missing_entities_raise_not_found() -> None:
    with Store().uow() as uow:
        with pytest.raises(NotFoundError):
            uow.scans.get(new_queued_scan().id)
        with pytest.raises(NotFoundError):
            uow.tenants.get_repository(REPO_ID)


def test_fetcher_releases_workspace_on_error() -> None:
    fetcher = FakeFetcher()
    with (
        pytest.raises(RuntimeError),
        fetcher.checkout(
            installation_id=1,
            repo=RepoRef("a", "b"),
            commit=new_queued_scan().commit,
            limits=FetchLimits(max_bytes=1, max_files=1, max_duration=timedelta(seconds=1)),
        ),
    ):
        raise RuntimeError
    assert fetcher.released == 1


def test_permanent_error_carries_reason_code() -> None:
    assert RepositoryFetchError("boom").reason_code is ReasonCode.FETCH_FAILED


@pytest.mark.parametrize(
    "build",
    [
        lambda: WorkspaceHandle("../etc"),
        lambda: ScanLimits(max_duration=timedelta(0), max_output_bytes=1, max_results=1),
        lambda: ScanLimits(max_duration=timedelta(minutes=1), max_output_bytes=0, max_results=1),
        lambda: ScanLimits(max_duration=timedelta(minutes=1), max_output_bytes=1, max_results=0),
        lambda: FetchLimits(max_bytes=0, max_files=1, max_duration=timedelta(seconds=1)),
        lambda: FetchLimits(max_bytes=1, max_files=0, max_duration=timedelta(seconds=1)),
        lambda: FetchLimits(max_bytes=1, max_files=1, max_duration=timedelta(hours=2)),
        lambda: PageRequest(limit=201),
        lambda: PageRequest(cursor="../x"),
        lambda: FindingQuery(repository_id=REPO_ID, only_new=True),
        lambda: VcsTarget(installation_id=0, repo=RepoRef("a", "b")),
        lambda: PrCommentBody("no marker"),
        lambda: PrCommentBody(""),
        lambda: ScanLogEntry(seq=1, at=NOW, level=LogLevel.INFO, message="a\x1bb"),
        lambda: ScanLogEntry(seq=1, at=NOW, level=LogLevel.INFO, message=""),
        lambda: AuditEntry(
            occurred_at=NOW,
            action="Bad Action",
            actor=None,
            organization_id=None,
            target_type="scan",
            target_id="1",
        ),
        lambda: AuditEntry(
            occurred_at=NOW,
            action="scan." + "a" * 33,
            actor=None,
            organization_id=None,
            target_type="scan",
            target_id="1",
        ),
        lambda: AuditEntry(
            occurred_at=NOW,
            action="scan.started",
            actor=None,
            organization_id=None,
            target_type="Scan!",
            target_id="1",
        ),
        lambda: AuditEntry(
            occurred_at=NOW,
            action="scan.started",
            actor=None,
            organization_id=None,
            target_type="scan",
            target_id="",
        ),
        lambda: AuditEntry(
            occurred_at=NOW,
            action="scan.started",
            actor=None,
            organization_id=None,
            target_type="scan",
            target_id="1",
            details={"k": "a\nb"},
        ),
        lambda: AuditEntry(
            occurred_at=NOW,
            action="scan.started",
            actor=None,
            organization_id=None,
            target_type="scan",
            target_id="1",
            details={str(i): "v" for i in range(17)},
        ),
        lambda: _output(scanner_version="bad version"),
        lambda: _output(image_digest="latest"),
        lambda: _output(output_sha256="x"),
        lambda: _output(results=[]),
    ],
)
def test_dto_validation(build: object) -> None:
    with pytest.raises(InvalidValueError):
        build()  # type: ignore[operator]


def _output(**overrides: object) -> ScannerOutput:
    values: dict[str, object] = {
        "kind": ScannerKind("semgrep"),
        "scanner_version": "1.0.0",
        "image_digest": DIGEST,
        "output_sha256": OUTPUT_SHA,
        "results": (),
        "truncated": False,
    }
    values.update(overrides)
    return ScannerOutput(**values)  # type: ignore[arg-type]


def test_audit_details_are_frozen_copies() -> None:
    details = {"role": "ADMIN"}
    entry = AuditEntry(
        occurred_at=NOW,
        action="membership.changed",
        actor=None,
        organization_id=None,
        target_type="user",
        target_id="u",
        details=details,
    )
    details["role"] = "VIEWER"
    assert entry.details["role"] == "ADMIN"
    with pytest.raises(TypeError):
        entry.details["role"] = "x"  # type: ignore[index]
    assert replace(entry, target_id="v").target_id == "v"


def test_valid_dtos() -> None:
    assert PageRequest(limit=10, cursor="abc").cursor == "abc"
    assert FindingQuery(repository_id=REPO_ID, only_new=False).seen_in_scan is None
    assert ScanLogEntry(seq=1, at=NOW, level=LogLevel.INFO, message="ok").seq == 1
    assert VcsTarget(installation_id=1, repo=RepoRef("a", "b")).installation_id == 1
    assert PrCommentBody("<!-- nave:pr-summary -->\nok").markdown
