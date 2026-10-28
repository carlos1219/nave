from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Self

from nave.domain.errors import InvalidTimestampError, ScanInvariantError
from nave.domain.events import ScanQueued, ScanStatusChanged
from nave.domain.policies import allowed_reasons, ensure_scan_transition
from nave.domain.timestamps import ensure_utc
from nave.domain.validation import is_int_in_range
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import RepositoryId, ScanId, UserId
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.status_reason import ReasonCode, StatusReason
from nave.domain.value_objects.trigger import ScanTrigger, TriggerKey

MAX_PULL_REQUEST_NUMBER: Final = 2**31 - 1
MAX_VERSION: Final = 2**63 - 1


@dataclass(frozen=True, slots=True, kw_only=True)
class Scan:
    """Un escaneo de un commit concreto. Inmutable.

    Cada transición devuelve un `Scan` nuevo y el evento correspondiente; el original
    no cambia. Las invariantes se comprueban en cada construcción, incluida la
    rehidratación desde la base de datos, de modo que una fila corrupta se detecta al
    cargarla y no más tarde.

    `version` pertenece al bloqueo optimista del repositorio: el dominio la conserva
    pero no la modifica.
    """

    id: ScanId
    repository_id: RepositoryId
    commit: CommitSha
    ref: GitRef
    trigger: ScanTrigger
    trigger_key: TriggerKey
    status: ScanStatus
    queued_at: datetime
    pull_request_number: int | None = None
    requested_by: UserId | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status_reason: StatusReason | None = None
    superseded_by: ScanId | None = None
    summary: ScanSummary | None = None
    version: int = 0

    def __post_init__(self) -> None:
        self._check_timestamps()
        self._check_status_consistency()
        self._check_reason()
        self._check_origin()
        if not is_int_in_range(self.version, 0, MAX_VERSION):
            raise ScanInvariantError("version must be a non-negative integer")

    @classmethod
    def queue(  # noqa: PLR0913 - todos los campos son obligatorios para crear un escaneo
        cls,
        *,
        scan_id: ScanId,
        repository_id: RepositoryId,
        commit: CommitSha,
        ref: GitRef,
        trigger: ScanTrigger,
        trigger_key: TriggerKey,
        at: datetime,
        pull_request_number: int | None = None,
        requested_by: UserId | None = None,
    ) -> tuple[Self, ScanQueued]:
        scan = cls(
            id=scan_id,
            repository_id=repository_id,
            commit=commit,
            ref=ref,
            trigger=trigger,
            trigger_key=trigger_key,
            status=ScanStatus.QUEUED,
            queued_at=at,
            pull_request_number=pull_request_number,
            requested_by=requested_by,
        )
        event = ScanQueued(
            occurred_at=at,
            scan_id=scan_id,
            repository_id=repository_id,
            commit=commit,
            ref=ref,
            trigger=trigger,
        )
        return scan, event

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    def start_fetching(self, at: datetime) -> tuple[Self, ScanStatusChanged]:
        return self._transition(ScanStatus.FETCHING, at)

    def start_scanning(self, at: datetime) -> tuple[Self, ScanStatusChanged]:
        return self._transition(ScanStatus.SCANNING, at)

    def start_processing(self, at: datetime) -> tuple[Self, ScanStatusChanged]:
        return self._transition(ScanStatus.PROCESSING, at)

    def complete(self, at: datetime, summary: ScanSummary) -> tuple[Self, ScanStatusChanged]:
        return self._transition(ScanStatus.COMPLETED, at, summary=summary)

    def fail(self, at: datetime, reason: StatusReason) -> tuple[Self, ScanStatusChanged]:
        return self._transition(ScanStatus.FAILED, at, reason=reason)

    def time_out(self, at: datetime, reason: StatusReason) -> tuple[Self, ScanStatusChanged]:
        return self._transition(ScanStatus.TIMED_OUT, at, reason=reason)

    def cancel(self, at: datetime) -> tuple[Self, ScanStatusChanged]:
        return self._transition(
            ScanStatus.CANCELLED, at, reason=StatusReason(ReasonCode.USER_CANCELLED)
        )

    def supersede(self, by: ScanId, at: datetime) -> tuple[Self, ScanStatusChanged]:
        """Cancela este escaneo porque otro más reciente de la misma ref lo sustituye."""
        return self._transition(
            ScanStatus.CANCELLED,
            at,
            reason=StatusReason(ReasonCode.SUPERSEDED),
            superseded_by=by,
        )

    def _transition(
        self,
        target: ScanStatus,
        at: datetime,
        *,
        reason: StatusReason | None = None,
        superseded_by: ScanId | None = None,
        summary: ScanSummary | None = None,
    ) -> tuple[Self, ScanStatusChanged]:
        ensure_scan_transition(self.status, target)
        ensure_utc(at, "at")
        if at < self._latest_timestamp():
            raise InvalidTimestampError("at", "must not precede the scan's latest timestamp")
        updated = replace(
            self,
            status=target,
            started_at=at if target is ScanStatus.FETCHING else self.started_at,
            finished_at=at if target.is_terminal else None,
            status_reason=reason,
            superseded_by=superseded_by,
            summary=summary,
        )
        event = ScanStatusChanged(
            occurred_at=at,
            scan_id=self.id,
            previous=self.status,
            current=target,
            reason=reason,
        )
        return updated, event

    def _latest_timestamp(self) -> datetime:
        return self.finished_at or self.started_at or self.queued_at

    def _check_timestamps(self) -> None:
        ensure_utc(self.queued_at, "queued_at")
        if self.started_at is not None:
            ensure_utc(self.started_at, "started_at")
            if self.started_at < self.queued_at:
                raise ScanInvariantError("started_at precedes queued_at")
        if self.finished_at is not None:
            ensure_utc(self.finished_at, "finished_at")
            if self.finished_at < (self.started_at or self.queued_at):
                raise ScanInvariantError("finished_at precedes the previous timestamp")

    def _check_status_consistency(self) -> None:
        status = self.status
        if status is ScanStatus.QUEUED and self.started_at is not None:
            raise ScanInvariantError("a queued scan cannot have started_at")
        if status.is_running and self.started_at is None:
            raise ScanInvariantError(f"a {status.value} scan requires started_at")
        if status in {ScanStatus.COMPLETED, ScanStatus.FAILED} and self.started_at is None:
            raise ScanInvariantError(f"a {status.value} scan requires started_at")
        if status.is_terminal != (self.finished_at is not None):
            raise ScanInvariantError("finished_at must be set exactly for terminal states")
        if (status is ScanStatus.COMPLETED) != (self.summary is not None):
            raise ScanInvariantError("summary must be set exactly for completed scans")

    def _check_reason(self) -> None:
        allowed = allowed_reasons(self.status)
        if not allowed:
            if self.status_reason is not None:
                raise ScanInvariantError(f"a {self.status.value} scan cannot have a reason")
        elif self.status_reason is None or self.status_reason.code not in allowed:
            codes = sorted(code.value for code in allowed)
            raise ScanInvariantError(f"a {self.status.value} scan requires a reason in {codes}")
        is_superseded = (
            self.status_reason is not None and self.status_reason.code is ReasonCode.SUPERSEDED
        )
        if is_superseded != (self.superseded_by is not None):
            raise ScanInvariantError("superseded_by must be set exactly for superseded scans")
        if self.superseded_by is not None and self.superseded_by == self.id:
            raise ScanInvariantError("a scan cannot supersede itself")

    def _check_origin(self) -> None:
        if self.trigger is ScanTrigger.MANUAL and self.requested_by is None:
            raise ScanInvariantError("a manual scan requires requested_by")
        if self.trigger is ScanTrigger.WEBHOOK and self.requested_by is not None:
            raise ScanInvariantError("a webhook scan cannot have requested_by")
        number = self.pull_request_number
        if number is not None and not is_int_in_range(number, 1, MAX_PULL_REQUEST_NUMBER):
            raise ScanInvariantError("pull_request_number is out of range")
