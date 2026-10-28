import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from nave.application.dto.audit import AuditEntry
from nave.application.ports.messaging import ScanQueuePort, ScanStep
from nave.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.application.services.scan_scheduler import ScanScheduler, ScheduleOutcome
from nave.domain.entities.tenancy import can_be_scanned
from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str, is_int_in_range
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.trigger import ScanTrigger, TriggerKey

_DELIVERY_ID: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.ASCII
)
_ACTION: Final = re.compile(r"[a-z_]{1,32}", re.ASCII)
_ZERO_SHA: Final = CommitSha("0" * 40)
_BRANCH_PREFIX: Final = "refs/heads/"
_SCANNED_PR_ACTIONS: Final = frozenset({"opened", "synchronize", "reopened"})
_MAX_ID: Final = 2**63 - 1


class GithubEvent(Enum):
    PUSH = "push"
    PULL_REQUEST = "pull_request"


class WebhookOutcome(Enum):
    ENQUEUED = "ENQUEUED"
    IGNORED = "IGNORED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True, kw_only=True)
class GithubEventCommand:
    """Evento ya autenticado (HMAC verificado en delivery) y extraído del payload.

    Del payload solo se toman identificadores numéricos, el SHA, la ref y el número de
    PR. Nunca URLs ni nombres: el repositorio se resuelve por su ID de GitHub (AM-206).
    """

    delivery_id: str
    event: GithubEvent
    action: str | None
    installation_id: int
    github_repo_id: int
    commit: CommitSha
    ref: GitRef
    pull_request_number: int | None = None
    from_fork: bool = False

    def __post_init__(self) -> None:
        if not fullmatch_str(_DELIVERY_ID, self.delivery_id):
            raise InvalidValueError("delivery_id", "must be a canonical lowercase UUID")
        if self.action is not None and not fullmatch_str(_ACTION, self.action):
            raise InvalidValueError("action", "is not a valid action")
        for name, value in (
            ("installation_id", self.installation_id),
            ("github_repo_id", self.github_repo_id),
        ):
            if not is_int_in_range(value, 1, _MAX_ID):
                raise InvalidValueError(name, "must be a positive integer")
        if (self.event is GithubEvent.PULL_REQUEST) != (self.pull_request_number is not None):
            raise InvalidValueError(
                "pull_request_number", "required exactly for pull_request events"
            )


@dataclass(frozen=True, slots=True)
class HandleGithubEventResult:
    outcome: WebhookOutcome
    scan_id: ScanId | None = None


class HandleGithubEvent:
    """Webhook de GitHub → escaneo. Idempotente por `X-GitHub-Delivery` (CLAUDE.md §9)."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        queue: ScanQueuePort,
        scheduler: ScanScheduler,
        clock: Clock,
        scan_fork_pull_requests: bool,
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._scheduler = scheduler
        self._clock = clock
        self._scan_forks = scan_fork_pull_requests

    def execute(self, command: GithubEventCommand) -> HandleGithubEventResult:
        with self._uow_factory() as uow:
            if uow.webhook_deliveries.exists(command.delivery_id):
                return HandleGithubEventResult(WebhookOutcome.DUPLICATE)
            outcome, scheduled = self._decide(uow, command)
            uow.webhook_deliveries.add(
                command.delivery_id, command.event.value, self._clock.now(), outcome.value
            )
            uow.commit()
        if scheduled is None:
            return HandleGithubEventResult(outcome)
        for superseded in scheduled.superseded:
            self._queue.revoke(superseded)
        if scheduled.scan.status is ScanStatus.QUEUED:
            self._queue.enqueue(ScanStep.EXECUTE, scheduled.scan.id)
        return HandleGithubEventResult(outcome, scheduled.scan.id)

    def _decide(
        self, uow: UnitOfWork, command: GithubEventCommand
    ) -> tuple[WebhookOutcome, ScheduleOutcome | None]:
        if not self._is_scannable_event(command):
            return WebhookOutcome.IGNORED, None
        repository = uow.tenants.find_repository_by_github_id(command.github_repo_id)
        if repository is None:
            return WebhookOutcome.IGNORED, None
        organization = uow.tenants.get_organization(repository.organization_id)
        if organization.github_installation_id != command.installation_id:
            # El evento dice venir de una instalación que no es la dueña del repositorio.
            uow.audit.append(
                AuditEntry(
                    occurred_at=self._clock.now(),
                    action="webhook.rejected",
                    actor=None,
                    organization_id=organization.id,
                    target_type="webhook_delivery",
                    target_id=command.delivery_id,
                    details={"reason": "installation_mismatch"},
                )
            )
            return WebhookOutcome.REJECTED, None
        if not can_be_scanned(repository, organization):
            return WebhookOutcome.IGNORED, None
        scheduled = self._scheduler.schedule(
            uow,
            repository=repository,
            commit=command.commit,
            ref=command.ref,
            trigger=ScanTrigger.WEBHOOK,
            trigger_key=TriggerKey(f"gh-delivery:{command.delivery_id}"),
            pull_request_number=command.pull_request_number,
        )
        return WebhookOutcome.ENQUEUED, scheduled

    def _is_scannable_event(self, command: GithubEventCommand) -> bool:
        if command.event is GithubEvent.PUSH:
            # Borrado de rama (SHA a ceros) y tags no se escanean.
            return command.commit != _ZERO_SHA and command.ref.value.startswith(_BRANCH_PREFIX)
        if command.action not in _SCANNED_PR_ACTIONS:
            return False
        return self._scan_forks or not command.from_fork
