from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from nave.application.dto.views import FindingQuery, PageRequest
from nave.application.errors import (
    AccessDeniedError,
    FetchUnavailableError,
    IdempotencyConflictError,
    LastAdminError,
    NotFoundError,
    RepositoryFetchError,
    RepositoryNotScannableError,
    ScanTimeoutError,
)
from nave.application.ports.messaging import ScanStep
from nave.application.ports.persistence import PolicyRecord
from nave.application.use_cases.abandon_scan import AbandonScanCommand
from nave.application.use_cases.cancel_scan import CancelScanCommand
from nave.application.use_cases.execute_scan import (
    ExecuteScan,
    ExecuteScanCommand,
    ExecuteScanOutcome,
)
from nave.application.use_cases.handle_github_event import (
    GithubEvent,
    GithubEventCommand,
    WebhookOutcome,
)
from nave.application.use_cases.manage_membership import ManageMembershipCommand
from nave.application.use_cases.process_scan_results import ProcessScanResultsCommand
from nave.application.use_cases.queries import GetScanQuery, ListFindingsQuery, ListScanLogsQuery
from nave.application.use_cases.report_to_vcs import ReportOutcome, ReportToVcsCommand
from nave.application.use_cases.start_scan import StartScanCommand
from nave.application.use_cases.triage_finding import TriageFindingCommand
from nave.domain.entities.scan import Scan
from nave.domain.errors import (
    InvalidFindingTextError,
    InvalidScanTransitionError,
    InvalidValueError,
    RedundantTriageDecisionError,
)
from nave.domain.events import ScanQueued
from nave.domain.quality_gates import Gate, GateScope, QualityGatePolicy, ReasonKind, Verdict
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import FindingId, RepositoryId, ScanId
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.status_reason import ReasonCode
from nave.domain.value_objects.triage_state import TriageState
from tests.fakes.memory import FakeRegistry, FakeScanner
from tests.unit.application.builders import (
    ADMIN,
    FETCH_LIMITS,
    GITHUB_REPO_ID,
    INSTALLATION_ID,
    ORG_ID,
    OUTSIDER,
    REPO_ID,
    SCAN_LIMITS,
    TRIAGER,
    VIEWER,
    output,
    sast,
)
from tests.unit.application.harness import Harness, make_harness

COMMIT = CommitSha("1" * 40)
OTHER_COMMIT = CommitSha("2" * 40)
MAIN = GitRef("refs/heads/main")
FEATURE = GitRef("feature/login")
REQUEST = "11111111-1111-4111-8111-111111111111"


def request_id(n: int) -> str:
    return f"{n:08x}-1111-4111-8111-111111111111"


def start(h: Harness, *, ref: GitRef = MAIN, commit: CommitSha = COMMIT, n: int = 1) -> ScanId:
    return h.start.execute(
        StartScanCommand(
            actor=TRIAGER, repository_id=REPO_ID, commit=commit, ref=ref, request_id=request_id(n)
        )
    ).scan_id


def scan_of(h: Harness, scan_id: ScanId) -> Scan:
    return h.store.state.scans[scan_id]


def run(h: Harness, scan_id: ScanId) -> ExecuteScanOutcome:
    h.clock.advance(minutes=1)
    return h.execute.execute(ExecuteScanCommand(scan_id))


def set_policy(h: Harness) -> None:
    policy = QualityGatePolicy(
        version=1,
        gates=(Gate("no-new-high", GateScope.NEW_FINDINGS, {Severity.HIGH: 0}),),
        sla_days={},
        fail_on_sla_breach=False,
    )
    h.store.state.policies[ORG_ID] = PolicyRecord(policy_id="p1", version=1, policy=policy)


class TestStartScan:
    def test_triager_queues_scan(self) -> None:
        h = make_harness()
        result = h.start.execute(
            StartScanCommand(
                actor=TRIAGER, repository_id=REPO_ID, commit=COMMIT, ref=MAIN, request_id=REQUEST
            )
        )
        assert result.created
        assert scan_of(h, result.scan_id).status is ScanStatus.QUEUED
        assert h.queue.enqueued == [(ScanStep.EXECUTE, result.scan_id)]
        assert [e.action for e in h.store.state.audit] == ["scan.requested"]
        assert isinstance(h.store.state.outbox[0], ScanQueued)
        assert h.store.state.logs[result.scan_id][0].message == "Scan queued"

    def test_same_request_is_idempotent(self) -> None:
        h = make_harness()
        first, second = start(h), start(h)
        assert first == second
        assert len(h.store.state.scans) == 1
        assert len(h.store.state.audit) == 1

    def test_reused_request_id_for_other_commit_conflicts(self) -> None:
        h = make_harness()
        start(h)
        with pytest.raises(IdempotencyConflictError):
            start(h, commit=OTHER_COMMIT)

    def test_viewer_is_denied_and_audited(self) -> None:
        h = make_harness()
        with pytest.raises(AccessDeniedError):
            h.start.execute(
                StartScanCommand(
                    actor=VIEWER, repository_id=REPO_ID, commit=COMMIT, ref=MAIN, request_id=REQUEST
                )
            )
        assert h.store.state.scans == {}
        assert h.store.state.audit[0].action == "access.denied"

    def test_outsider_cannot_learn_the_repository_exists(self) -> None:
        h = make_harness()
        unknown = RepositoryId(UUID("00000000-0000-4000-8000-00000000ffff"))
        with pytest.raises(NotFoundError) as foreign:
            h.start.execute(
                StartScanCommand(
                    actor=OUTSIDER,
                    repository_id=REPO_ID,
                    commit=COMMIT,
                    ref=MAIN,
                    request_id=REQUEST,
                )
            )
        with pytest.raises(NotFoundError) as missing:
            h.start.execute(
                StartScanCommand(
                    actor=OUTSIDER,
                    repository_id=unknown,
                    commit=COMMIT,
                    ref=MAIN,
                    request_id=REQUEST,
                )
            )
        assert type(foreign.value) is type(missing.value)

    def test_disabled_repository_is_rejected(self) -> None:
        h = make_harness()
        repo = h.store.state.repositories[REPO_ID]
        h.store.state.repositories[REPO_ID] = replace(repo, enabled=False)
        with pytest.raises(RepositoryNotScannableError):
            start(h)

    def test_new_scan_supersedes_pending_scan_of_same_branch(self) -> None:
        h = make_harness()
        old = start(h, n=1)
        new = start(h, commit=OTHER_COMMIT, n=2)
        assert scan_of(h, old).status is ScanStatus.CANCELLED
        assert scan_of(h, old).superseded_by == new
        assert h.queue.revoked == [old]
        # F7-15: el escaneo nuevo debe existir cuando se enlaza desde el antiguo (FK inmediata).
        assert scan_of(h, new).status is ScanStatus.QUEUED

    def test_processing_scan_is_not_superseded(self) -> None:
        h = make_harness()
        old = start(h, n=1)
        scan = scan_of(h, old)
        for step in ("start_fetching", "start_scanning", "start_processing"):
            scan, _ = getattr(scan, step)(h.clock.now())
        h.store.state.scans[old] = scan
        start(h, commit=OTHER_COMMIT, n=2)
        assert scan_of(h, old).status is ScanStatus.PROCESSING

    def test_request_id_must_be_canonical(self) -> None:
        with pytest.raises(InvalidValueError):
            StartScanCommand(
                actor=TRIAGER, repository_id=REPO_ID, commit=COMMIT, ref=MAIN, request_id="abc"
            )


def push(
    delivery: str = "72d3162e-cc78-11e3-81ab-4c9367dc0958",
    *,
    commit: CommitSha = COMMIT,
    ref: GitRef = MAIN,
    installation: int = INSTALLATION_ID,
    repo: int = GITHUB_REPO_ID,
) -> GithubEventCommand:
    return GithubEventCommand(
        delivery_id=delivery,
        event=GithubEvent.PUSH,
        action=None,
        installation_id=installation,
        github_repo_id=repo,
        commit=commit,
        ref=ref,
    )


def pull_request(action: str = "opened", *, from_fork: bool = False) -> GithubEventCommand:
    return GithubEventCommand(
        delivery_id="72d3162e-cc78-11e3-81ab-4c9367dc0959",
        event=GithubEvent.PULL_REQUEST,
        action=action,
        installation_id=INSTALLATION_ID,
        github_repo_id=GITHUB_REPO_ID,
        commit=COMMIT,
        ref=FEATURE,
        pull_request_number=12,
        from_fork=from_fork,
    )


class TestWebhook:
    def test_push_enqueues_scan(self) -> None:
        h = make_harness()
        result = h.webhook.execute(push())
        assert result.outcome is WebhookOutcome.ENQUEUED
        assert result.scan_id is not None
        scan = scan_of(h, result.scan_id)
        assert scan.trigger_key.value == "gh-delivery:72d3162e-cc78-11e3-81ab-4c9367dc0958"
        assert h.store.state.deliveries == {"72d3162e-cc78-11e3-81ab-4c9367dc0958": "ENQUEUED"}
        assert h.queue.enqueued == [(ScanStep.EXECUTE, result.scan_id)]

    def test_redelivery_is_a_duplicate(self) -> None:
        h = make_harness()
        h.webhook.execute(push())
        assert h.webhook.execute(push()).outcome is WebhookOutcome.DUPLICATE
        assert len(h.store.state.scans) == 1

    @pytest.mark.parametrize(
        "command",
        [
            push(commit=CommitSha("0" * 40)),
            push(ref=GitRef("refs/tags/v1.0")),
            push(repo=999),
            pull_request("closed"),
        ],
    )
    def test_ignored_events(self, command: GithubEventCommand) -> None:
        h = make_harness()
        assert h.webhook.execute(command).outcome is WebhookOutcome.IGNORED
        assert h.store.state.scans == {}
        assert h.store.state.deliveries[command.delivery_id] == "IGNORED"

    def test_fork_pull_requests_follow_configuration(self) -> None:
        assert (
            make_harness(scan_forks=False).webhook.execute(pull_request(from_fork=True)).outcome
            is WebhookOutcome.IGNORED
        )
        result = make_harness(scan_forks=True).webhook.execute(pull_request(from_fork=True))
        assert result.outcome is WebhookOutcome.ENQUEUED

    def test_installation_mismatch_is_rejected_and_audited(self) -> None:
        h = make_harness()
        assert h.webhook.execute(push(installation=1)).outcome is WebhookOutcome.REJECTED
        assert h.store.state.audit[0].details["reason"] == "installation_mismatch"
        assert h.store.state.scans == {}

    def test_disabled_repository_is_ignored(self) -> None:
        h = make_harness()
        h.store.state.repositories[REPO_ID] = replace(
            h.store.state.repositories[REPO_ID], enabled=False
        )
        assert h.webhook.execute(push()).outcome is WebhookOutcome.IGNORED

    @pytest.mark.parametrize(
        "overrides",
        [
            {"delivery_id": "not-a-guid"},
            {"action": "Opened!"},
            {"installation_id": 0},
            {"pull_request_number": 3},
        ],
    )
    def test_command_validation(self, overrides: dict[str, object]) -> None:
        values = {k: getattr(push(), k) for k in push().__slots__}
        values.update(overrides)
        with pytest.raises(InvalidValueError):
            GithubEventCommand(**values)


class TestExecuteScan:
    def test_no_enabled_scanners_fails_instead_of_passing(self) -> None:
        h = make_harness()
        scan_id = start(h)
        empty = ExecuteScan(
            uow_factory=h.store.uow,
            fetcher=h.fetcher,
            registry=FakeRegistry(),
            processor=h.process,
            queue=h.queue,
            clock=h.clock,
            fetch_limits=FETCH_LIMITS,
            scan_limits=SCAN_LIMITS,
        )
        h.clock.advance(minutes=1)
        assert empty.execute(ExecuteScanCommand(scan_id)) is ExecuteScanOutcome.FAILED
        scan = scan_of(h, scan_id)
        assert scan.status is ScanStatus.FAILED
        assert h.fetcher.released == 0
        assert (ScanStep.REPORT, scan_id) in h.queue.enqueued

    def test_happy_path(self) -> None:
        h = make_harness()
        scan_id = start(h)
        assert run(h, scan_id) is ExecuteScanOutcome.COMPLETED
        scan = scan_of(h, scan_id)
        assert scan.status is ScanStatus.COMPLETED
        assert scan.summary is not None
        assert scan.summary.new.high == 1
        assert scan.summary.verdict is Verdict.NEUTRAL
        assert h.fetcher.released == 1
        assert len(h.scanner.calls) == 1
        assert (ScanStep.REPORT, scan_id) in h.queue.enqueued
        (occurrence,) = h.store.state.occurrences.values()
        assert occurrence.is_new

    def test_policy_failure_is_recorded(self) -> None:
        h = make_harness()
        set_policy(h)
        scan_id = start(h)
        run(h, scan_id)
        assert h.store.state.evaluations[scan_id].verdict is Verdict.FAIL
        assert scan_of(h, scan_id).summary.verdict is Verdict.FAIL  # type: ignore[union-attr]

    def test_findings_are_fixed_and_reopened_on_default_branch(self) -> None:
        h = make_harness()
        run(h, start(h, n=1))
        h.scanner.output = output()
        run(h, start(h, commit=OTHER_COMMIT, n=2))
        (finding,) = h.store.state.findings.values()
        assert finding.triage_state is TriageState.FIXED
        h.scanner.output = output(sast())
        third = start(h, commit=COMMIT, n=3)
        run(h, third)
        (finding,) = h.store.state.findings.values()
        assert finding.triage_state is TriageState.OPEN
        assert [d.state for d in h.store.state.decisions] == [TriageState.FIXED, TriageState.OPEN]
        assert all(d.is_system for d in h.store.state.decisions)

    def test_fixing_pages_through_every_missing_finding(self) -> None:
        h = make_harness(fix_page_size=2)
        rules = [f"rule.{n}" for n in range(5)]
        h.scanner.output = output(*(sast(rule) for rule in rules))
        run(h, start(h, n=1))
        h.scanner.output = output()
        run(h, start(h, commit=OTHER_COMMIT, n=2))
        findings = h.store.state.findings.values()
        assert len(findings) == 5
        assert {f.triage_state for f in findings} == {TriageState.FIXED}

    def test_pr_reintroducing_fixed_finding_fails_gate_without_reopening(self) -> None:
        h = make_harness()
        set_policy(h)
        run(h, start(h, n=1))
        h.scanner.output = output()
        run(h, start(h, commit=OTHER_COMMIT, n=2))
        h.scanner.output = output(sast())
        pr_scan = start(h, ref=FEATURE, n=3)
        run(h, pr_scan)
        assert h.store.state.evaluations[pr_scan].verdict is Verdict.FAIL
        (finding,) = h.store.state.findings.values()
        assert finding.triage_state is TriageState.FIXED

    def test_incomplete_results_never_fix_findings_nor_pass(self) -> None:
        h = make_harness()
        set_policy(h)
        run(h, start(h, n=1))
        h.scanner.output = output(truncated=True)
        second = start(h, commit=OTHER_COMMIT, n=2)
        run(h, second)
        (finding,) = h.store.state.findings.values()
        assert finding.triage_state is TriageState.OPEN
        verdict = h.store.state.evaluations[second]
        assert verdict.verdict is Verdict.NEUTRAL
        assert verdict.reasons[-1].kind is ReasonKind.TRUNCATED

    def test_permanent_fetch_error_fails_with_sanitized_reason(self) -> None:
        h = make_harness()
        h.fetcher.error = RepositoryFetchError("fatal:\x1b[31m clone failed")
        scan_id = start(h)
        assert run(h, scan_id) is ExecuteScanOutcome.FAILED
        scan = scan_of(h, scan_id)
        assert scan.status_reason is not None
        assert scan.status_reason.code is ReasonCode.FETCH_FAILED
        assert "\x1b" not in scan.status_reason.detail
        assert (ScanStep.REPORT, scan_id) in h.queue.enqueued

    def test_scanner_timeout(self) -> None:
        h = make_harness(scanner_output=ScanTimeoutError())
        scan_id = start(h)
        assert run(h, scan_id) is ExecuteScanOutcome.TIMED_OUT
        assert scan_of(h, scan_id).status is ScanStatus.TIMED_OUT
        assert h.fetcher.released == 1

    def test_transient_error_propagates_and_retry_succeeds(self) -> None:
        h = make_harness()
        h.fetcher.error = FetchUnavailableError()
        scan_id = start(h)
        with pytest.raises(FetchUnavailableError):
            run(h, scan_id)
        assert scan_of(h, scan_id).status is ScanStatus.FETCHING
        assert h.store.state.logs[scan_id][-1].level.value == "WARNING"
        h.fetcher.error = None
        assert run(h, scan_id) is ExecuteScanOutcome.COMPLETED

    def test_retry_in_processing_rescans_and_completes(self) -> None:
        h = make_harness()
        scan_id = start(h)
        scan = scan_of(h, scan_id)
        for step in ("start_fetching", "start_scanning", "start_processing"):
            scan, _ = getattr(scan, step)(h.clock.now())
        h.store.state.scans[scan_id] = scan
        assert run(h, scan_id) is ExecuteScanOutcome.COMPLETED

    def test_terminal_scan_is_skipped(self) -> None:
        h = make_harness()
        scan_id = start(h)
        run(h, scan_id)
        assert run(h, scan_id) is ExecuteScanOutcome.SKIPPED
        assert len(h.scanner.calls) == 1

    def test_cancellation_during_fetch_stops_and_releases_workspace(self) -> None:
        h = make_harness()
        scan_id = start(h)
        original = h.fetcher.checkout

        def cancelling_checkout(**kwargs: object) -> object:
            h.cancel.execute(CancelScanCommand(actor=TRIAGER, scan_id=scan_id))
            return original(**kwargs)  # type: ignore[arg-type]

        h.fetcher.checkout = cancelling_checkout  # type: ignore[assignment, method-assign]
        assert run(h, scan_id) is ExecuteScanOutcome.SKIPPED
        assert h.scanner.calls == []
        assert h.fetcher.released == 1

    def test_output_from_another_scanner_is_rejected(self) -> None:
        h = make_harness(scanner_output=output(sast(), kind="trivy"))
        scan_id = start(h)
        assert run(h, scan_id) is ExecuteScanOutcome.FAILED
        assert scan_of(h, scan_id).status_reason.code is ReasonCode.SCANNER_OUTPUT_INVALID  # type: ignore[union-attr]

    def test_repository_disabled_after_queueing_fails(self) -> None:
        h = make_harness()
        scan_id = start(h)
        h.store.state.repositories[REPO_ID] = replace(
            h.store.state.repositories[REPO_ID], enabled=False
        )
        assert run(h, scan_id) is ExecuteScanOutcome.FAILED
        assert h.fetcher.checkouts == []
        assert scan_of(h, scan_id).status_reason.code is ReasonCode.INTERNAL_ERROR  # type: ignore[union-attr]

    def test_process_on_terminal_scan_is_a_no_op(self) -> None:
        h = make_harness()
        scan_id = start(h)
        run(h, scan_id)
        before = scan_of(h, scan_id)
        assert h.process.execute(ProcessScanResultsCommand(scan_id, ())) == before


class TestReport:
    def test_completed_pull_request_scan_reports_everything(self) -> None:
        h = make_harness()
        result = h.webhook.execute(pull_request())
        assert result.scan_id is not None
        run(h, result.scan_id)
        assert h.report.execute(ReportToVcsCommand(result.scan_id)) is ReportOutcome.REPORTED
        assert len(h.vcs.checks) == 1
        assert h.vcs.sarif[0][2] == 1
        (_, _, body) = h.vcs.comments[0]
        assert body.markdown.startswith("<!-- nave:pr-summary -->")
        assert h.vcs.checks[0][0].installation_id == INSTALLATION_ID

    def test_push_scan_has_no_comment(self) -> None:
        h = make_harness()
        scan_id = start(h)
        run(h, scan_id)
        h.report.execute(ReportToVcsCommand(scan_id))
        assert h.vcs.comments == []
        assert len(h.vcs.sarif) == 1

    def test_failed_scan_only_updates_the_check(self) -> None:
        h = make_harness()
        h.fetcher.error = RepositoryFetchError("x")
        scan_id = start(h)
        run(h, scan_id)
        assert h.report.execute(ReportToVcsCommand(scan_id)) is ReportOutcome.REPORTED
        assert (len(h.vcs.checks), h.vcs.sarif, h.vcs.comments) == (1, [], [])

    def test_cancelled_and_running_scans_are_skipped(self) -> None:
        h = make_harness()
        queued = start(h, n=1)
        assert h.report.execute(ReportToVcsCommand(queued)) is ReportOutcome.SKIPPED
        h.cancel.execute(CancelScanCommand(actor=TRIAGER, scan_id=queued))
        assert h.report.execute(ReportToVcsCommand(queued)) is ReportOutcome.SKIPPED
        assert h.vcs.checks == []


class TestCancel:
    def test_triager_cancels_queued_scan(self) -> None:
        h = make_harness()
        scan_id = start(h)
        assert h.cancel.execute(CancelScanCommand(actor=TRIAGER, scan_id=scan_id)).cancelled
        assert scan_of(h, scan_id).status is ScanStatus.CANCELLED
        assert h.queue.revoked == [scan_id]
        assert h.store.state.audit[-1].action == "scan.cancelled"

    def test_cancelling_a_finished_scan_is_idempotent(self) -> None:
        h = make_harness()
        scan_id = start(h)
        run(h, scan_id)
        assert not h.cancel.execute(CancelScanCommand(actor=TRIAGER, scan_id=scan_id)).cancelled

    def test_processing_scan_cannot_be_cancelled(self) -> None:
        h = make_harness()
        scan_id = start(h)
        scan = scan_of(h, scan_id)
        for step in ("start_fetching", "start_scanning", "start_processing"):
            scan, _ = getattr(scan, step)(h.clock.now())
        h.store.state.scans[scan_id] = scan
        with pytest.raises(InvalidScanTransitionError):
            h.cancel.execute(CancelScanCommand(actor=TRIAGER, scan_id=scan_id))

    def test_viewer_cannot_cancel(self) -> None:
        h = make_harness()
        scan_id = start(h)
        with pytest.raises(AccessDeniedError):
            h.cancel.execute(CancelScanCommand(actor=VIEWER, scan_id=scan_id))


def completed_finding(h: Harness) -> FindingId:
    run(h, start(h))
    (finding,) = h.store.state.findings.values()
    return finding.id


class TestTriage:
    def test_accept_risk(self) -> None:
        h = make_harness()
        finding_id = completed_finding(h)
        result = h.triage.execute(
            TriageFindingCommand(
                actor=TRIAGER,
                finding_id=finding_id,
                state=TriageState.ACCEPTED_RISK,
                justification="Mitigated by WAF rule 1234 until Q1 refactor.",
                expires_at=h.clock.now() + timedelta(days=30),
            )
        )
        assert result.state is TriageState.ACCEPTED_RISK
        finding = h.store.state.findings[finding_id]
        assert finding.triage_state is TriageState.ACCEPTED_RISK
        assert h.store.state.decisions[-1].id == result.decision_id
        assert h.store.state.audit[-1].action == "finding.triaged"
        assert "expires_at" in h.store.state.audit[-1].details

    def test_viewer_cannot_triage(self) -> None:
        h = make_harness()
        finding_id = completed_finding(h)
        with pytest.raises(AccessDeniedError):
            h.triage.execute(
                TriageFindingCommand(
                    actor=VIEWER,
                    finding_id=finding_id,
                    state=TriageState.FALSE_POSITIVE,
                    justification="Test fixture, never deployed to production.",
                )
            )

    def test_justification_is_validated_before_anything_else(self) -> None:
        h = make_harness()
        finding_id = completed_finding(h)
        with pytest.raises(InvalidFindingTextError):
            h.triage.execute(
                TriageFindingCommand(
                    actor=TRIAGER,
                    finding_id=finding_id,
                    state=TriageState.FALSE_POSITIVE,
                    justification="ok",
                )
            )

    def test_redundant_decision(self) -> None:
        h = make_harness()
        finding_id = completed_finding(h)
        with pytest.raises(RedundantTriageDecisionError):
            h.triage.execute(
                TriageFindingCommand(actor=TRIAGER, finding_id=finding_id, state=TriageState.OPEN)
            )


class TestMembership:
    def test_admin_changes_roles_with_audit(self) -> None:
        h = make_harness()
        h.members.execute(
            ManageMembershipCommand(
                actor=ADMIN, organization_id=ORG_ID, user=VIEWER, role=Role.TRIAGER
            )
        )
        assert h.store.state.memberships[(VIEWER, ORG_ID)] is Role.TRIAGER
        assert h.store.state.audit[-1].details == {"previous": "VIEWER", "current": "TRIAGER"}

    def test_cannot_remove_the_last_admin(self) -> None:
        h = make_harness()
        with pytest.raises(LastAdminError):
            h.members.execute(
                ManageMembershipCommand(actor=ADMIN, organization_id=ORG_ID, user=ADMIN, role=None)
            )

    def test_admin_may_step_down_when_another_admin_exists(self) -> None:
        h = make_harness()
        h.members.execute(
            ManageMembershipCommand(
                actor=ADMIN, organization_id=ORG_ID, user=TRIAGER, role=Role.ADMIN
            )
        )
        h.members.execute(
            ManageMembershipCommand(actor=ADMIN, organization_id=ORG_ID, user=ADMIN, role=None)
        )
        assert (ADMIN, ORG_ID) not in h.store.state.memberships

    def test_no_op_is_not_audited(self) -> None:
        h = make_harness()
        h.members.execute(
            ManageMembershipCommand(
                actor=ADMIN, organization_id=ORG_ID, user=VIEWER, role=Role.VIEWER
            )
        )
        assert h.store.state.audit == []

    def test_removing_or_demoting_an_admin_locks_the_admins_first(self) -> None:
        # F7-14: sin el bloqueo, dos admins que se retiran a la vez dejan la org sin ninguno.
        h = make_harness()
        h.members.execute(
            ManageMembershipCommand(
                actor=ADMIN, organization_id=ORG_ID, user=TRIAGER, role=Role.ADMIN
            )
        )
        assert h.store.state.admin_locks == []  # subir a alguien no necesita el bloqueo
        h.members.execute(
            ManageMembershipCommand(
                actor=ADMIN, organization_id=ORG_ID, user=TRIAGER, role=Role.VIEWER
            )
        )
        assert h.store.state.admin_locks == [ORG_ID]

    def test_changing_a_non_admin_never_takes_the_admin_lock(self) -> None:
        h = make_harness()
        h.members.execute(
            ManageMembershipCommand(
                actor=ADMIN, organization_id=ORG_ID, user=VIEWER, role=Role.TRIAGER
            )
        )
        h.members.execute(
            ManageMembershipCommand(actor=ADMIN, organization_id=ORG_ID, user=VIEWER, role=None)
        )
        assert h.store.state.admin_locks == []

    def test_triager_cannot_manage_members(self) -> None:
        h = make_harness()
        with pytest.raises(AccessDeniedError):
            h.members.execute(
                ManageMembershipCommand(
                    actor=TRIAGER, organization_id=ORG_ID, user=TRIAGER, role=Role.ADMIN
                )
            )


class TestAbandon:
    def test_queued_scan_expires(self) -> None:
        h = make_harness()
        scan_id = start(h)
        assert h.abandon.execute(
            AbandonScanCommand(scan_id=scan_id, reason_code=ReasonCode.INTERNAL_ERROR)
        )
        scan = scan_of(h, scan_id)
        assert scan.status is ScanStatus.TIMED_OUT
        assert scan.status_reason.code is ReasonCode.QUEUE_EXPIRED  # type: ignore[union-attr]
        assert h.queue.revoked == [scan_id]
        assert (ScanStep.REPORT, scan_id) in h.queue.enqueued

    def test_running_scan_fails_after_retries(self) -> None:
        h = make_harness()
        h.fetcher.error = FetchUnavailableError()
        scan_id = start(h)
        with pytest.raises(FetchUnavailableError):
            run(h, scan_id)
        h.abandon.execute(
            AbandonScanCommand(
                scan_id=scan_id, reason_code=ReasonCode.INTERNAL_ERROR, detail="retries\nexhausted"
            )
        )
        scan = scan_of(h, scan_id)
        assert scan.status is ScanStatus.FAILED
        assert scan.status_reason.detail == "retries?exhausted"  # type: ignore[union-attr]

    def test_timeout_of_running_scan(self) -> None:
        h = make_harness()
        h.fetcher.error = FetchUnavailableError()
        scan_id = start(h)
        with pytest.raises(FetchUnavailableError):
            run(h, scan_id)
        h.abandon.execute(AbandonScanCommand(scan_id=scan_id, reason_code=ReasonCode.TIMEOUT))
        assert scan_of(h, scan_id).status is ScanStatus.TIMED_OUT

    def test_nothing_to_abandon(self) -> None:
        h = make_harness()
        scan_id = start(h)
        scan = scan_of(h, scan_id)
        for step in ("start_fetching", "start_scanning", "start_processing"):
            scan, _ = getattr(scan, step)(h.clock.now())
        h.store.state.scans[scan_id] = scan
        assert not h.abandon.execute(
            AbandonScanCommand(scan_id=scan_id, reason_code=ReasonCode.TIMEOUT)
        )
        run(h, scan_id)
        assert not h.abandon.execute(
            AbandonScanCommand(scan_id=scan_id, reason_code=ReasonCode.INTERNAL_ERROR)
        )

    def test_only_operational_reasons(self) -> None:
        with pytest.raises(InvalidValueError):
            AbandonScanCommand(scan_id=ScanId(UUID(int=1)), reason_code=ReasonCode.USER_CANCELLED)


class TestQueries:
    def test_list_findings_and_filter_by_scan(self) -> None:
        h = make_harness()
        scan_id = start(h)
        run(h, scan_id)
        page = h.list_findings.execute(
            ListFindingsQuery(
                actor=VIEWER,
                filters=FindingQuery(repository_id=REPO_ID, seen_in_scan=scan_id, only_new=True),
                page=PageRequest(limit=10),
            )
        )
        assert len(page.items) == 1
        assert page.items[0].occurrence is not None

    def test_scan_filter_from_another_repository_looks_like_not_found(self) -> None:
        h = make_harness()
        scan_id = start(h)
        foreign = replace(scan_of(h, scan_id), repository_id=RepositoryId(UUID(int=99)))
        h.store.state.scans[scan_id] = foreign
        with pytest.raises(NotFoundError):
            h.list_findings.execute(
                ListFindingsQuery(
                    actor=VIEWER,
                    filters=FindingQuery(repository_id=REPO_ID, seen_in_scan=scan_id),
                    page=PageRequest(),
                )
            )

    def test_get_scan_and_logs(self) -> None:
        h = make_harness()
        scan_id = start(h)
        run(h, scan_id)
        assert (
            h.get_scan.execute(GetScanQuery(actor=VIEWER, scan_id=scan_id)).status
            is ScanStatus.COMPLETED
        )
        logs = h.logs.execute(
            ListScanLogsQuery(actor=VIEWER, scan_id=scan_id, after_seq=1, limit=2)
        )
        assert [entry.seq for entry in logs] == [2, 3]

    def test_outsider_sees_nothing(self) -> None:
        h = make_harness()
        scan_id = start(h)
        with pytest.raises(NotFoundError):
            h.get_scan.execute(GetScanQuery(actor=OUTSIDER, scan_id=scan_id))
        with pytest.raises(NotFoundError):
            h.logs.execute(ListScanLogsQuery(actor=OUTSIDER, scan_id=scan_id))

    @pytest.mark.parametrize(("after", "limit"), [(-1, 10), (0, 0), (0, 501)])
    def test_log_query_bounds(self, after: int, limit: int) -> None:
        with pytest.raises(InvalidValueError):
            ListScanLogsQuery(
                actor=VIEWER, scan_id=ScanId(UUID(int=1)), after_seq=after, limit=limit
            )


def test_scanner_fake_kind() -> None:
    assert FakeScanner("trivy", output()).kind.value == "trivy"
