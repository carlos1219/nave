"""Eventos de dominio. Inmutables; se publican mediante el outbox transaccional."""

from dataclasses import dataclass
from datetime import datetime

from nave.domain.timestamps import ensure_utc
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import (
    FindingId,
    RepositoryId,
    ScanId,
    TriageDecisionId,
)
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import StatusReason
from nave.domain.value_objects.triage_state import TriageState
from nave.domain.value_objects.trigger import ScanTrigger


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    occurred_at: datetime

    def __post_init__(self) -> None:
        ensure_utc(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class ScanQueued(DomainEvent):
    scan_id: ScanId
    repository_id: RepositoryId
    commit: CommitSha
    ref: GitRef
    trigger: ScanTrigger


@dataclass(frozen=True, slots=True, kw_only=True)
class ScanStatusChanged(DomainEvent):
    scan_id: ScanId
    previous: ScanStatus
    current: ScanStatus
    reason: StatusReason | None


@dataclass(frozen=True, slots=True, kw_only=True)
class FindingTriaged(DomainEvent):
    finding_id: FindingId
    decision_id: TriageDecisionId
    previous: TriageState
    current: TriageState
    by_system: bool
