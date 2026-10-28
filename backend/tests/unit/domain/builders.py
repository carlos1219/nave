"""Constructores compartidos por los tests del dominio."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.quality_gates import Verdict
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import FindingTitle, Justification
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import (
    FindingId,
    RepositoryId,
    ScanId,
    TriageDecisionId,
    UserId,
)
from nave.domain.value_objects.rule_id import CweId, RuleId
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.triage_state import TriageState
from nave.domain.value_objects.trigger import ScanTrigger, TriggerKey

T0 = datetime(2026, 10, 11, 8, 0, 0, tzinfo=UTC)
SCAN_ID = ScanId(UUID("00000000-0000-4000-8000-000000000001"))
OTHER_SCAN_ID = ScanId(UUID("00000000-0000-4000-8000-000000000002"))
REPOSITORY_ID = RepositoryId(UUID("00000000-0000-4000-8000-0000000000aa"))
USER_ID = UserId(UUID("00000000-0000-4000-8000-0000000000bb"))
COMMIT = CommitSha("0123456789abcdef0123456789abcdef01234567")
SUMMARY = ScanSummary(
    new=SeverityCounts(high=1), open=SeverityCounts(high=2), verdict=Verdict.PASS, truncated=False
)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


RUNNING_PATH = ("FETCHING", "SCANNING", "PROCESSING", "COMPLETED")


def advance(scan: Scan, target: str) -> Scan:
    """Lleva un escaneo en cola hasta `target` por el camino feliz, un minuto por paso."""
    current = scan
    for minute, step in enumerate(RUNNING_PATH, start=1):
        moment = at(minute)
        if step == "FETCHING":
            current, _ = current.start_fetching(moment)
        elif step == "SCANNING":
            current, _ = current.start_scanning(moment)
        elif step == "PROCESSING":
            current, _ = current.start_processing(moment)
        else:
            current, _ = current.complete(moment, SUMMARY)
        if step == target:
            return current
    raise AssertionError(f"unknown target {target}")


def new_queued_scan(
    *,
    trigger: ScanTrigger = ScanTrigger.WEBHOOK,
    requested_by: UserId | None = None,
    pull_request_number: int | None = None,
) -> Scan:
    scan, _ = Scan.queue(
        scan_id=SCAN_ID,
        repository_id=REPOSITORY_ID,
        commit=COMMIT,
        ref=GitRef("refs/heads/main"),
        trigger=trigger,
        trigger_key=TriggerKey("gh-delivery:72d3162e-cc78-11e3-81ab-4c9367dc0958"),
        at=T0,
        pull_request_number=pull_request_number,
        requested_by=requested_by,
    )
    return scan


FINDING_ID = FindingId(UUID("00000000-0000-4000-8000-0000000000f1"))
DECISION_ID = TriageDecisionId(UUID("00000000-0000-4000-8000-0000000000d1"))
FINGERPRINT = Fingerprint("ab" * 32)
JUSTIFICATION = Justification("Validated with the security team; input is constant.")


def new_finding(*, severity: Severity = Severity.HIGH) -> Finding:
    return Finding.discover(
        finding_id=FINDING_ID,
        repository_id=REPOSITORY_ID,
        fingerprint=FINGERPRINT,
        scanner_kind=ScannerKind("semgrep"),
        category=FindingCategory.SAST,
        rule_id=RuleId("python.lang.security.audit.dangerous-subprocess-use"),
        severity=severity,
        title=FindingTitle("Dangerous subprocess use"),
        scan_id=SCAN_ID,
        at=T0,
        cwe=(CweId("CWE-78"),),
    )


def user_decision(
    state: TriageState,
    *,
    minutes: int = 10,
    justification: Justification | None = JUSTIFICATION,
    expires_in: timedelta | None = None,
) -> TriageDecision:
    moment = at(minutes)
    return TriageDecision.by_user(
        decision_id=DECISION_ID,
        finding_id=FINDING_ID,
        state=state,
        user=USER_ID,
        at=moment,
        justification=justification,
        expires_at=moment + expires_in if expires_in is not None else None,
    )


def system_decision(state: TriageState, *, minutes: int = 10) -> TriageDecision:
    return TriageDecision.by_system(
        decision_id=DECISION_ID,
        finding_id=FINDING_ID,
        state=state,
        at=at(minutes),
        justification=Justification("Not observed in the latest default-branch scan."),
    )
