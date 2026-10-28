"""Conversión fila ↔ entidad. Toda fila leída se reconstruye con los constructores del
dominio: una fila incoherente falla al cargarse, no más tarde."""

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import Row

from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.tenancy import Organization, OrganizationStatus, Repository
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import (
    FindingMessage,
    FindingTitle,
    Justification,
    Snippet,
)
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import (
    FindingId,
    OrganizationId,
    RepositoryId,
    ScanId,
    TriageDecisionId,
    UserId,
)
from nave.domain.value_objects.location import SourceLocation
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.rule_id import CveId, CweId, RuleId
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.source_path import SourcePath
from nave.domain.value_objects.status_reason import ReasonCode, StatusReason
from nave.domain.value_objects.triage_state import TriageState
from nave.domain.value_objects.trigger import ScanTrigger, TriggerKey
from nave.infrastructure.persistence.codec import decode_summary, encode_summary

type Values = dict[str, Any]


def _opt[T](value: Any, build: Callable[[Any], T]) -> T | None:
    return None if value is None else build(value)


# --- Tenancy ------------------------------------------------------------------------


def organization_from_row(row: Row[Any]) -> Organization:
    return Organization(
        id=OrganizationId(row.id),
        github_installation_id=row.github_installation_id,
        account_login=row.account_login,
        status=OrganizationStatus.parse(row.status),
        github_account_id=row.github_account_id,
    )


def repository_from_row(row: Row[Any]) -> Repository:
    return Repository(
        id=RepositoryId(row.id),
        organization_id=OrganizationId(row.organization_id),
        github_repo_id=row.github_repo_id,
        ref=RepoRef(owner=row.owner, name=row.name),
        default_branch=GitRef(row.default_branch),
        enabled=row.enabled,
    )


# --- Scan ---------------------------------------------------------------------------


def scan_values(scan: Scan) -> Values:
    """Columnas mutables + identidad. `version` la gestiona el repositorio."""
    reason = scan.status_reason
    return {
        "id": scan.id.value,
        "repository_id": scan.repository_id.value,
        "commit_sha": scan.commit.value,
        "ref": scan.ref.value,
        "trigger": scan.trigger.value,
        "trigger_key": scan.trigger_key.value,
        "status": scan.status.value,
        "queued_at": scan.queued_at,
        "pull_request_number": scan.pull_request_number,
        "requested_by": scan.requested_by.value if scan.requested_by else None,
        "started_at": scan.started_at,
        "finished_at": scan.finished_at,
        "status_reason_code": reason.code.value if reason else None,
        "status_reason_detail": reason.detail if reason else None,
        "superseded_by": scan.superseded_by.value if scan.superseded_by else None,
        "summary": encode_summary(scan.summary) if scan.summary else None,
    }


def scan_from_row(row: Row[Any]) -> Scan:
    reason = (
        StatusReason(ReasonCode.parse(row.status_reason_code), row.status_reason_detail or "")
        if row.status_reason_code is not None
        else None
    )
    return Scan(
        id=ScanId(row.id),
        repository_id=RepositoryId(row.repository_id),
        commit=CommitSha(row.commit_sha),
        ref=GitRef(row.ref),
        trigger=ScanTrigger.parse(row.trigger),
        trigger_key=TriggerKey(row.trigger_key),
        status=ScanStatus.parse(row.status),
        queued_at=row.queued_at,
        pull_request_number=row.pull_request_number,
        requested_by=_opt(row.requested_by, UserId),
        started_at=row.started_at,
        finished_at=row.finished_at,
        status_reason=reason,
        superseded_by=_opt(row.superseded_by, ScanId),
        summary=decode_summary(row.summary) if row.summary is not None else None,
        version=row.version,
    )


# --- Finding ------------------------------------------------------------------------


def finding_values(finding: Finding) -> Values:
    return {
        "id": finding.id.value,
        "repository_id": finding.repository_id.value,
        "fingerprint": finding.fingerprint.value,
        "scanner_kind": finding.scanner_kind.value,
        "category": finding.category.value,
        "rule_id": finding.rule_id.value,
        "severity": finding.severity.value,
        "title": finding.title.value,
        "first_seen_scan_id": finding.first_seen_scan_id.value,
        "last_seen_scan_id": finding.last_seen_scan_id.value,
        "first_seen_at": finding.first_seen_at,
        "last_seen_at": finding.last_seen_at,
        "cwe": [c.value for c in finding.cwe],
        "cve": [c.value for c in finding.cve],
        "triage_state": finding.triage_state.value,
        "triage_expires_at": finding.triage_expires_at,
    }


def finding_from_row(row: Row[Any] | Mapping[str, Any]) -> Finding:
    data: Mapping[Any, Any] = row._mapping if isinstance(row, Row) else row
    return Finding(
        id=FindingId(data["id"]),
        repository_id=RepositoryId(data["repository_id"]),
        fingerprint=Fingerprint(data["fingerprint"]),
        scanner_kind=ScannerKind(data["scanner_kind"]),
        category=FindingCategory.parse(data["category"]),
        rule_id=RuleId(data["rule_id"]),
        severity=Severity.parse(data["severity"]),
        title=FindingTitle(data["title"]),
        first_seen_scan_id=ScanId(data["first_seen_scan_id"]),
        last_seen_scan_id=ScanId(data["last_seen_scan_id"]),
        first_seen_at=data["first_seen_at"],
        last_seen_at=data["last_seen_at"],
        cwe=tuple(CweId(c) for c in data["cwe"]),
        cve=tuple(CveId(c) for c in data["cve"]),
        triage_state=TriageState.parse(data["triage_state"]),
        triage_expires_at=data["triage_expires_at"],
        version=data["version"],
    )


def occurrence_values(occurrence: FindingOccurrence) -> Values:
    location = occurrence.location
    return {
        "scan_id": occurrence.scan_id.value,
        "finding_id": occurrence.finding_id.value,
        "path": location.path.value if location else None,
        "start_line": location.start_line if location else None,
        "end_line": location.end_line if location else None,
        "message": occurrence.message.value,
        "snippet": occurrence.snippet.value if occurrence.snippet else None,
        "is_new": occurrence.is_new,
    }


def occurrence_from_mapping(data: Mapping[str, Any]) -> FindingOccurrence:
    location = (
        SourceLocation(SourcePath(data["path"]), data["start_line"], data["end_line"])
        if data["path"] is not None
        else None
    )
    return FindingOccurrence(
        scan_id=ScanId(data["scan_id"]),
        finding_id=FindingId(data["finding_id"]),
        location=location,
        message=FindingMessage(data["message"]),
        snippet=_opt(data["snippet"], Snippet),
        is_new=data["is_new"],
    )


def decision_values(decision: TriageDecision) -> Values:
    return {
        "id": decision.id.value,
        "finding_id": decision.finding_id.value,
        "state": decision.state.value,
        "decided_at": decision.decided_at,
        "decided_by": decision.decided_by.value if decision.decided_by else None,
        "justification": decision.justification.value if decision.justification else None,
        "expires_at": decision.expires_at,
    }


def decision_from_row(row: Row[Any]) -> TriageDecision:
    return TriageDecision(
        id=TriageDecisionId(row.id),
        finding_id=FindingId(row.finding_id),
        state=TriageState.parse(row.state),
        decided_at=row.decided_at,
        decided_by=_opt(row.decided_by, UserId),
        justification=Justification(row.justification) if row.justification else None,
        expires_at=row.expires_at,
    )
