"""Composición de los casos de uso con fakes: un composition root de pruebas."""

from dataclasses import dataclass

from nave.application.dto.scanning import ScannerOutput
from nave.application.services.authorization import Authorizer
from nave.application.services.scan_scheduler import ScanScheduler
from nave.application.use_cases.abandon_scan import AbandonScan
from nave.application.use_cases.cancel_scan import CancelScan
from nave.application.use_cases.execute_scan import ExecuteScan
from nave.application.use_cases.handle_github_event import HandleGithubEvent
from nave.application.use_cases.manage_membership import ManageMembership
from nave.application.use_cases.process_scan_results import ProcessScanResults
from nave.application.use_cases.queries import GetScan, ListFindings, ListScanLogs
from nave.application.use_cases.report_to_vcs import ReportToVcs
from nave.application.use_cases.start_scan import StartScan
from nave.application.use_cases.triage_finding import TriageFinding
from tests.fakes.memory import (
    DomainPolicyEvaluator,
    FakeClock,
    FakeFetcher,
    FakeRegistry,
    FakeScanner,
    RecordingQueue,
    RecordingVcs,
    SequentialIds,
    Store,
)
from tests.unit.application.builders import (
    FETCH_LIMITS,
    SCAN_LIMITS,
    TEST_KEY,
    output,
    sast,
    seed_tenancy,
    seed_users,
)


@dataclass
class Harness:
    store: Store
    clock: FakeClock
    queue: RecordingQueue
    fetcher: FakeFetcher
    scanner: FakeScanner
    vcs: RecordingVcs
    start: StartScan
    webhook: HandleGithubEvent
    execute: ExecuteScan
    process: ProcessScanResults
    report: ReportToVcs
    cancel: CancelScan
    triage: TriageFinding
    members: ManageMembership
    abandon: AbandonScan
    list_findings: ListFindings
    get_scan: GetScan
    logs: ListScanLogs


def make_harness(
    *,
    scanner_output: ScannerOutput | Exception | None = None,
    scan_forks: bool = True,
    fix_page_size: int = 500,
) -> Harness:
    store = Store()
    seed_tenancy(store)
    seed_users(store)
    clock = FakeClock()
    ids = SequentialIds()
    queue = RecordingQueue()
    fetcher = FakeFetcher()
    scanner = FakeScanner(
        "semgrep", scanner_output if scanner_output is not None else output(sast())
    )
    vcs = RecordingVcs()
    authorizer = Authorizer(uow_factory=store.uow, clock=clock)
    scheduler = ScanScheduler(clock=clock, ids=ids)
    process = ProcessScanResults(
        uow_factory=store.uow,
        queue=queue,
        evaluator=DomainPolicyEvaluator(),
        clock=clock,
        ids=ids,
        max_findings_per_scanner=100,
        fingerprint_key=TEST_KEY,
        fix_page_size=fix_page_size,
    )
    return Harness(
        store=store,
        clock=clock,
        queue=queue,
        fetcher=fetcher,
        scanner=scanner,
        vcs=vcs,
        start=StartScan(
            uow_factory=store.uow,
            queue=queue,
            scheduler=scheduler,
            authorizer=authorizer,
            clock=clock,
        ),
        webhook=HandleGithubEvent(
            uow_factory=store.uow,
            queue=queue,
            scheduler=scheduler,
            clock=clock,
            scan_fork_pull_requests=scan_forks,
        ),
        execute=ExecuteScan(
            uow_factory=store.uow,
            fetcher=fetcher,
            registry=FakeRegistry(scanner),
            processor=process,
            queue=queue,
            clock=clock,
            fetch_limits=FETCH_LIMITS,
            scan_limits=SCAN_LIMITS,
        ),
        process=process,
        report=ReportToVcs(uow_factory=store.uow, vcs=vcs, max_sarif_findings=1000),
        cancel=CancelScan(uow_factory=store.uow, queue=queue, authorizer=authorizer, clock=clock),
        triage=TriageFinding(uow_factory=store.uow, authorizer=authorizer, clock=clock, ids=ids),
        members=ManageMembership(uow_factory=store.uow, authorizer=authorizer, clock=clock),
        abandon=AbandonScan(uow_factory=store.uow, queue=queue, clock=clock),
        list_findings=ListFindings(uow_factory=store.uow, authorizer=authorizer),
        get_scan=GetScan(uow_factory=store.uow, authorizer=authorizer),
        logs=ListScanLogs(uow_factory=store.uow, authorizer=authorizer),
    )
