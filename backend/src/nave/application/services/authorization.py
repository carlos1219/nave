import logging
from typing import Final

from nave.application.dto.audit import AuditEntry
from nave.application.errors import AccessDeniedError, NotFoundError
from nave.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role

_LOG: Final = logging.getLogger("nave.authz")


class Authorizer:
    """Autorización por organización dentro de cada caso de uso (CLAUDE.md §12).

    - Sin pertenencia a la organización: `NotFoundError`, como si el recurso no
      existiera, para no revelar recursos de otras organizaciones (AM-106). Es un sondeo
      que cualquier usuario autenticado puede repetir sin límite y contra identificadores
      que ni siquiera existen: se registra como aviso estructurado (fuente de alertas) y
      **no** en el audit log, que quedaría inundado y mezclaría ruido ajeno en la cadena de
      una organización (F7-16, ADR 0026).
    - Con pertenencia pero rol insuficiente: `AccessDeniedError`, auditado en una
      transacción propia: la del caso de uso se deshace al propagarse el error y se llevaría
      el registro consigo. Solo lo provocan miembros de esa organización.
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def require(  # noqa: PLR0913 - el contexto de auditoría forma parte del contrato
        self,
        uow: UnitOfWork,
        *,
        actor: UserId,
        organization_id: OrganizationId,
        required: Role,
        target_type: str,
        target_id: str,
    ) -> Role:
        role = uow.memberships.role_of(actor, organization_id)
        if role is not None and role.satisfies(required):
            return role
        if role is None:
            _LOG.warning(
                "access denied to a non-member",
                extra={
                    "action": "access.denied",
                    "user_id": str(actor),
                    "organization_id": str(organization_id),
                    "target_type": target_type,
                    "target_id": target_id,
                },
            )
            raise NotFoundError(target_type)
        self._record_denial(
            actor=actor,
            organization_id=organization_id,
            required=required,
            target_type=target_type,
            target_id=target_id,
        )
        raise AccessDeniedError(f"role {required.value} required")

    def _record_denial(
        self,
        *,
        actor: UserId,
        organization_id: OrganizationId,
        required: Role,
        target_type: str,
        target_id: str,
    ) -> None:
        with self._uow_factory() as audit_uow:
            audit_uow.audit.append(
                AuditEntry(
                    occurred_at=self._clock.now(),
                    action="access.denied",
                    actor=actor,
                    organization_id=organization_id,
                    target_type=target_type,
                    target_id=target_id,
                    details={"required_role": required.value, "member": "true"},
                )
            )
            audit_uow.commit()
