"""Los casos de uso de la fase 1, sobre PostgreSQL real en lugar de los fakes."""

from datetime import timedelta

import pytest
from sqlalchemy import Engine, func, select

from nave.application.dto.views import FindingQuery, PageRequest
from nave.application.errors import NotFoundError
from nave.application.ports.messaging import ScanStep
from nave.application.services.authorization import Authorizer
from nave.application.services.scan_scheduler import ScanScheduler
from nave.application.use_cases.execute_scan import (
    ExecuteScan,
    ExecuteScanCommand,
    ExecuteScanOutcome,
)
from nave.application.use_cases.process_scan_results import ProcessScanResults
from nave.application.use_cases.queries import ListFindings, ListFindingsQuery
from nave.application.use_cases.start_scan import StartScan, StartScanCommand
from nave.application.use_cases.triage_finding import TriageFinding, TriageFindingCommand
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.identifiers import UserId
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.triage_state import TriageState
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.audit import verify_audit_chain
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.fakes.memory import (
    DomainPolicyEvaluator,
    FakeClock,
    FakeFetcher,
    FakeRegistry,
    FakeScanner,
    RecordingQueue,
    SequentialIds,
)
from tests.integration.persistence.seed import ADMIN, MAIN, ORG, REPO, TRIAGER, seed_tenancy
from tests.unit.application.builders import FETCH_LIMITS, SCAN_LIMITS, TEST_KEY, output, sast

pytestmark = pytest.mark.integration


def test_a_new_scan_of_the_same_ref_supersedes_the_pending_one_on_postgres(engine: Engine) -> None:
    """F7-15: `scans.superseded_by` es una FK inmediata; con la base de datos real el
    escaneo antiguo solo puede enlazar a uno nuevo que ya exista. Con los fakes no se veía."""
    seed_tenancy(engine)
    uow = SqlUnitOfWorkFactory(engine)
    clock, ids, queue = FakeClock(), SequentialIds(), RecordingQueue()
    with uow() as tx:
        tx.memberships.set_role(TRIAGER, ORG, Role.TRIAGER, granted_by=None, at=clock.now())
        tx.commit()
    start = StartScan(
        uow_factory=uow,
        queue=queue,
        scheduler=ScanScheduler(clock=clock, ids=ids),
        authorizer=Authorizer(uow_factory=uow, clock=clock),
        clock=clock,
    )

    def request(commit: str, key: str) -> StartScanCommand:
        return StartScanCommand(
            actor=TRIAGER, repository_id=REPO, commit=CommitSha(commit), ref=MAIN, request_id=key
        )

    first = start.execute(request("a" * 40, "11111111-1111-4111-8111-111111111111")).scan_id
    second = start.execute(request("b" * 40, "22222222-2222-4222-8222-222222222222")).scan_id
    assert first != second
    with uow() as tx:
        assert tx.scans.get(first).status is ScanStatus.CANCELLED
        assert tx.scans.get(first).superseded_by == second
        assert tx.scans.get(second).status is ScanStatus.QUEUED
    assert queue.revoked == [first]
    assert (ScanStep.EXECUTE, second) in queue.enqueued


def test_scan_triage_and_audit_end_to_end(engine: Engine) -> None:
    seed_tenancy(engine)
    uow = SqlUnitOfWorkFactory(engine)
    clock, ids, queue = FakeClock(), SequentialIds(), RecordingQueue()
    with uow() as tx:
        tx.memberships.set_role(ADMIN, ORG, Role.ADMIN, granted_by=ADMIN, at=clock.now())
        tx.memberships.set_role(TRIAGER, ORG, Role.TRIAGER, granted_by=ADMIN, at=clock.now())
        tx.commit()
    authorizer = Authorizer(uow_factory=uow, clock=clock)
    start = StartScan(
        uow_factory=uow,
        queue=queue,
        scheduler=ScanScheduler(clock=clock, ids=ids),
        authorizer=authorizer,
        clock=clock,
    )
    process = ProcessScanResults(
        uow_factory=uow,
        queue=queue,
        evaluator=DomainPolicyEvaluator(),
        clock=clock,
        ids=ids,
        max_findings_per_scanner=100,
        fingerprint_key=TEST_KEY,
    )
    fetcher = FakeFetcher()
    execute = ExecuteScan(
        uow_factory=uow,
        fetcher=fetcher,
        registry=FakeRegistry(FakeScanner("semgrep", output(sast(), sast("rule.two")))),
        processor=process,
        queue=queue,
        clock=clock,
        fetch_limits=FETCH_LIMITS,
        scan_limits=SCAN_LIMITS,
    )

    request = "11111111-1111-4111-8111-111111111111"
    command = StartScanCommand(
        actor=TRIAGER, repository_id=REPO, commit=CommitSha("a" * 40), ref=MAIN, request_id=request
    )
    scan_id = start.execute(command).scan_id
    assert start.execute(command).scan_id == scan_id  # idempotente por request_id
    assert (ScanStep.EXECUTE, scan_id) in queue.enqueued

    clock.advance(minutes=1)
    assert execute.execute(ExecuteScanCommand(scan_id)) is ExecuteScanOutcome.COMPLETED
    assert execute.execute(ExecuteScanCommand(scan_id)) is ExecuteScanOutcome.SKIPPED
    assert fetcher.released == 1

    with uow() as tx:
        scan = tx.scans.get(scan_id)
        assert scan.status is ScanStatus.COMPLETED
        assert scan.summary is not None
        assert scan.summary.new.high == 2
        logs = tx.scan_logs.list(scan_id, after_seq=0, limit=50)
        assert logs
        assert [e.seq for e in logs] == list(range(1, len(logs) + 1))

    listing = ListFindings(uow_factory=uow, authorizer=authorizer)
    page = listing.execute(
        ListFindingsQuery(
            actor=TRIAGER,
            filters=FindingQuery(repository_id=REPO, seen_in_scan=scan_id, only_new=True),
            page=PageRequest(limit=1),
        )
    )
    assert len(page.items) == 1
    assert page.next_cursor is not None
    finding = page.items[0].finding

    clock.advance(minutes=1)
    triage = TriageFinding(uow_factory=uow, authorizer=authorizer, clock=clock, ids=ids)
    triage.execute(
        TriageFindingCommand(
            actor=TRIAGER,
            finding_id=finding.id,
            state=TriageState.ACCEPTED_RISK,
            justification="Vendor fix scheduled; compensating WAF rule in place.",
            expires_at=clock.now() + timedelta(days=30),
        )
    )
    outsider = UserId(finding.id.value)
    with pytest.raises(NotFoundError):
        listing.execute(
            ListFindingsQuery(
                actor=outsider, filters=FindingQuery(repository_id=REPO), page=PageRequest()
            )
        )

    with engine.connect() as conn:
        assert verify_audit_chain(conn) is None
        actions: list[str] = list(
            conn.execute(select(t.audit_log.c.action).order_by(t.audit_log.c.id)).scalars()
        )
        # El sondeo del ajeno (`outsider`) no se audita: queda como aviso en el log (ADR 0026).
        assert actions == ["scan.requested", "finding.triaged"]
        decisions = conn.execute(select(func.count()).select_from(t.triage_decisions)).scalar_one()
        assert decisions == 1
        outbox: list[str] = list(conn.execute(select(t.outbox_events.c.type)).scalars())
        assert "ScanQueued" in outbox
        assert "FindingTriaged" in outbox
    with uow() as tx:
        assert tx.findings.get(finding.id).triage_state is TriageState.ACCEPTED_RISK
