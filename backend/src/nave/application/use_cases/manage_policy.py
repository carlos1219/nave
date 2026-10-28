"""Edición de la política de quality gates por un ADMIN de la organización (ADR 0030).

Cada cambio crea una versión nueva y desactiva la anterior: las versiones no se reescriben,
así que una evaluación pasada siempre se puede explicar con la política que la produjo."""

from dataclasses import dataclass

from nave.application.dto.audit import AuditEntry
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.system import Clock, IdGenerator
from nave.application.services.authorization import Authorizer
from nave.domain.quality_gates import QualityGatePolicy
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdatePolicyCommand:
    actor: UserId
    organization_id: OrganizationId
    policy: QualityGatePolicy


class UpdatePolicy:
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

    def execute(self, command: UpdatePolicyCommand) -> int:
        now = self._clock.now()
        with self._uow_factory() as uow:
            self._authorizer.require(
                uow,
                actor=command.actor,
                organization_id=command.organization_id,
                required=Role.ADMIN,
                target_type="policy",
                target_id=str(command.organization_id),
            )
            version = uow.policies.publish(
                command.organization_id, command.policy, policy_id=self._ids.new_uuid(), at=now
            )
            uow.audit.append(
                AuditEntry(
                    occurred_at=now,
                    action="policy.changed",
                    actor=command.actor,
                    organization_id=command.organization_id,
                    target_type="policy",
                    target_id=str(command.organization_id),
                    details={
                        "version": str(version),
                        "gates": str(len(command.policy.gates)),
                        "sla": str(len(command.policy.sla_days)),
                    },
                )
            )
            uow.commit()
        return version
