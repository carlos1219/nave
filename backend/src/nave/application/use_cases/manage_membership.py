from dataclasses import dataclass
from datetime import datetime

from nave.application.dto.audit import AuditEntry
from nave.application.errors import LastAdminError
from nave.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.application.services.authorization import Authorizer
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role


@dataclass(frozen=True, slots=True, kw_only=True)
class ManageMembershipCommand:
    actor: UserId
    organization_id: OrganizationId
    user: UserId
    role: Role | None
    """`None` retira al usuario de la organización."""


def change_membership(  # noqa: PLR0913 - contexto del cambio y de su auditoría
    uow: UnitOfWork,
    *,
    actor: UserId,
    organization_id: OrganizationId,
    user: UserId,
    role: Role | None,
    at: datetime,
    via: str | None = None,
) -> bool:
    """Aplica el cambio ya autorizado. Nunca deja la organización sin admin. Devuelve
    `False` si no había nada que cambiar. No confirma la transacción."""
    uow.users.get(user)  # NotFoundError si el usuario nunca inició sesión
    previous = uow.memberships.role_of(user, organization_id)
    if previous is role:
        return False
    if previous is Role.ADMIN and role is not Role.ADMIN:
        # Se bloquean las filas de los admins antes de decidir: sin ello, dos admins
        # que se retiran mutuamente a la vez comprueban «quedan dos» y la organización
        # se queda sin ninguno. Tras esperar el bloqueo el rol pudo cambiar: se relee.
        admins = uow.memberships.lock_admins(organization_id)
        previous = uow.memberships.role_of(user, organization_id)
        if previous is role:
            return False
        if previous is Role.ADMIN and admins <= 1:
            raise LastAdminError("the organization must keep at least one admin")
    if role is None:
        uow.memberships.remove(user, organization_id)
    else:
        uow.memberships.set_role(user, organization_id, role, granted_by=actor, at=at)
    details = {
        "previous": previous.value if previous else "NONE",
        "current": role.value if role else "NONE",
    }
    if via is not None:
        details["via"] = via
    uow.audit.append(
        AuditEntry(
            occurred_at=at,
            action="membership.changed",
            actor=actor,
            organization_id=organization_id,
            target_type="user",
            target_id=str(user),
            details=details,
        )
    )
    return True


class ManageMembership:
    """Alta, cambio de rol o baja. Solo ADMIN. Nunca deja la organización sin admin."""

    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, authorizer: Authorizer, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._authorizer = authorizer
        self._clock = clock

    def execute(self, command: ManageMembershipCommand) -> None:
        with self._uow_factory() as uow:
            self._authorizer.require(
                uow,
                actor=command.actor,
                organization_id=command.organization_id,
                required=Role.ADMIN,
                target_type="organization",
                target_id=str(command.organization_id),
            )
            changed = change_membership(
                uow,
                actor=command.actor,
                organization_id=command.organization_id,
                user=command.user,
                role=command.role,
                at=self._clock.now(),
            )
            if changed:
                uow.commit()
