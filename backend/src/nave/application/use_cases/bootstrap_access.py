"""Primer administrador por CLI (ADR 0004, ADR 0016): el único camino sin actor previo.

No hay ruta HTTP ni variable de entorno que lo haga: requiere acceso al entorno de operación.
"""

from collections.abc import Sequence

from nave.application.dto.audit import AuditEntry
from nave.application.dto.auth import UserSummary
from nave.application.errors import NotFoundError
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.system import Clock
from nave.domain.entities.tenancy import OrganizationStatus
from nave.domain.value_objects.identifiers import UserId
from nave.domain.value_objects.role import Role


class AmbiguousOrganizationError(Exception):
    """Dos instalaciones activas con el mismo login: el operador debe resolverlo a mano."""


class BootstrapAccess:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def list_users(self, *, limit: int = 50) -> Sequence[UserSummary]:
        with self._uow_factory() as uow:
            return uow.users.list_recent(limit=limit)

    def list_platform_admins(self) -> Sequence[UserId]:
        with self._uow_factory() as uow:
            return uow.platform_admins.list_admins()

    def set_platform_admin(self, *, user: UserId, granted: bool) -> bool:
        """Concede o retira el rol de plataforma (ADR 0030). `True` si cambió algo."""
        now = self._clock.now()
        with self._uow_factory() as uow:
            uow.users.get(user)  # NotFoundError si nunca inició sesión
            changed = (
                uow.platform_admins.grant(user, now)
                if granted
                else uow.platform_admins.revoke(user)
            )
            if changed:
                uow.audit.append(
                    AuditEntry(
                        occurred_at=now,
                        action="platform_admin.granted" if granted else "platform_admin.revoked",
                        actor=None,
                        organization_id=None,
                        target_type="user",
                        target_id=str(user),
                    )
                )
            uow.commit()
        return changed

    def grant_admin(self, *, organization_login: str, user: UserId) -> None:
        now = self._clock.now()
        with self._uow_factory() as uow:
            uow.users.get(user)  # NotFoundError si nunca inició sesión
            matches = [
                o
                for o in uow.tenant_admin.find_organizations_by_login(organization_login)
                if o.status is not OrganizationStatus.UNINSTALLED
            ]
            if not matches:
                raise NotFoundError("organization")
            if len(matches) > 1:
                raise AmbiguousOrganizationError(organization_login)
            organization = matches[0]
            uow.memberships.set_role(user, organization.id, Role.ADMIN, granted_by=None, at=now)
            uow.audit.append(
                AuditEntry(
                    occurred_at=now,
                    action="membership.bootstrapped",
                    actor=None,
                    organization_id=organization.id,
                    target_type="user",
                    target_id=str(user),
                    details={"role": Role.ADMIN.value},
                )
            )
            uow.commit()
