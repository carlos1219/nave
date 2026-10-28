from dataclasses import dataclass
from datetime import datetime

from nave.application.dto.audit import AuditEntry
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.system import Clock, IdGenerator
from nave.application.services.authorization import Authorizer
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.value_objects.finding_text import Justification
from nave.domain.value_objects.identifiers import FindingId, TriageDecisionId, UserId
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.triage_state import TriageState


@dataclass(frozen=True, slots=True, kw_only=True)
class TriageFindingCommand:
    actor: UserId
    finding_id: FindingId
    state: TriageState
    justification: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TriageFindingResult:
    decision_id: TriageDecisionId
    state: TriageState


class TriageFinding:
    """Decisión de triage humana: auditada y append-only (CLAUDE.md §7, §12)."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        authorizer: Authorizer,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer
        self._clock = clock
        self._ids = ids

    def execute(self, command: TriageFindingCommand) -> TriageFindingResult:
        # La justificación se valida antes de tocar nada (texto de un usuario).
        justification = (
            Justification(command.justification) if command.justification is not None else None
        )
        with self._uow_factory() as uow:
            finding = uow.findings.get(command.finding_id)
            repository = uow.tenants.get_repository(finding.repository_id)
            self._authorizer.require(
                uow,
                actor=command.actor,
                organization_id=repository.organization_id,
                required=Role.TRIAGER,
                target_type="finding",
                target_id=str(finding.id),
            )
            now = self._clock.now()
            decision = TriageDecision.by_user(
                decision_id=TriageDecisionId(self._ids.new_uuid()),
                finding_id=finding.id,
                state=command.state,
                user=command.actor,
                at=now,
                justification=justification,
                expires_at=command.expires_at,
            )
            updated, event = finding.apply_triage(decision)
            uow.findings.save(updated)
            uow.findings.add_decision(decision)
            uow.events.publish([event])
            details = {"state": decision.state.value, "decision_id": str(decision.id)}
            if decision.expires_at is not None:
                details["expires_at"] = decision.expires_at.isoformat()
            uow.audit.append(
                AuditEntry(
                    occurred_at=now,
                    action="finding.triaged",
                    actor=command.actor,
                    organization_id=repository.organization_id,
                    target_type="finding",
                    target_id=str(finding.id),
                    details=details,
                )
            )
            uow.commit()
        return TriageFindingResult(decision_id=decision.id, state=decision.state)
