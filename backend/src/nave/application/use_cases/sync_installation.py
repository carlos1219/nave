"""Sincroniza una instalación de la GitHub App con la base de datos (ADR 0014).

Lo único que se toma del webhook es el id de la instalación. Todo lo demás (cuenta,
estado, repositorios) se pregunta a GitHub con la credencial de la App: así un payload
falsificado o manipulado no puede dar de alta una organización ni habilitar repositorios.

- Solo se registran cuentas de la allowlist (`NAVE_GITHUB_ALLOWED_ACCOUNTS`).
- La cuenta se fija por su id inmutable: si un login de la allowlist pasa a otra cuenta,
  no se hereda la organización (AM-809).
- Una reinstalación dentro del plazo de gracia recupera la organización y su historial.
- Las llamadas a GitHub se hacen antes de abrir la transacción.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from nave.application.dto.audit import AuditEntry
from nave.application.dto.installations import InstallationInfo, RemoteRepository, SyncOutcome
from nave.application.ports.installations import InstallationDirectoryPort
from nave.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from nave.application.ports.system import Clock, IdGenerator
from nave.domain.entities.tenancy import Organization, OrganizationStatus, Repository
from nave.domain.quality_gates import default_policy
from nave.domain.value_objects.identifiers import OrganizationId, RepositoryId

_ACTION_FOR_STATUS: Final = {
    OrganizationStatus.ACTIVE: "organization.activated",
    OrganizationStatus.SUSPENDED: "organization.suspended",
    OrganizationStatus.UNINSTALLED: "organization.uninstalled",
}


@dataclass(frozen=True, slots=True)
class SyncInstallationCommand:
    installation_id: int


class SyncInstallation:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        directory: InstallationDirectoryPort,
        clock: Clock,
        ids: IdGenerator,
        allowed_accounts: Collection[str],
    ) -> None:
        self._uow_factory = uow_factory
        self._directory = directory
        self._clock = clock
        self._ids = ids
        self._allowed = frozenset(a.lower() for a in allowed_accounts)

    def execute(self, command: SyncInstallationCommand) -> SyncOutcome:
        installation_id = command.installation_id
        info = self._directory.get_installation(installation_id)
        if info is None:
            return self._mark_uninstalled(installation_id)
        if info.account_login.lower() not in self._allowed:
            return self._not_allowed(info)
        repositories = (
            None if info.suspended else self._directory.list_repositories(installation_id)
        )
        return self._apply(info, repositories)

    # --- desinstalación y cuentas no permitidas --------------------------------------

    def _mark_uninstalled(self, installation_id: int) -> SyncOutcome:
        now = self._clock.now()
        with self._uow_factory() as uow:
            org = uow.tenant_admin.find_organization_by_installation(installation_id)
            if org is None:
                return SyncOutcome.UNKNOWN
            if org.status is not OrganizationStatus.UNINSTALLED:
                uow.tenant_admin.update_organization(
                    replace(org, status=OrganizationStatus.UNINSTALLED), uninstalled_at=now
                )
                self._audit(uow, org.id, "organization.uninstalled", now)
                uow.commit()
            return SyncOutcome.UNINSTALLED

    def _not_allowed(self, info: InstallationInfo) -> SyncOutcome:
        now = self._clock.now()
        with self._uow_factory() as uow:
            org = uow.tenant_admin.find_organization_by_installation(info.installation_id)
            if org is not None and org.status is OrganizationStatus.ACTIVE:
                uow.tenant_admin.update_organization(
                    replace(org, status=OrganizationStatus.SUSPENDED), uninstalled_at=None
                )
                self._audit(uow, org.id, "organization.suspended", now, reason="not_allowed")
                uow.commit()
        return SyncOutcome.IGNORED

    # --- alta y actualización ---------------------------------------------------------

    def _apply(
        self, info: InstallationInfo, remote: Sequence[RemoteRepository] | None
    ) -> SyncOutcome:
        now = self._clock.now()
        status = OrganizationStatus.SUSPENDED if info.suspended else OrganizationStatus.ACTIVE
        with self._uow_factory() as uow:
            admin = uow.tenant_admin
            current = admin.find_organization_by_installation(info.installation_id)
            if current is not None:
                if current.github_account_id not in (None, info.account_id):
                    self._audit(
                        uow, current.id, "installation.account_mismatch", now, reason="account_id"
                    )
                    uow.commit()
                    return SyncOutcome.IGNORED
                outcome = SyncOutcome.SUSPENDED if info.suspended else SyncOutcome.UPDATED
                org = self._update(uow, current, info, status, now)
            else:
                org, outcome = self._register(uow, info, status, now)
            if remote is not None:
                self._sync_repositories(uow, org, remote, now)
            uow.commit()
        return outcome

    def _update(
        self,
        uow: UnitOfWork,
        current: Organization,
        info: InstallationInfo,
        status: OrganizationStatus,
        now: datetime,
    ) -> Organization:
        updated = replace(
            current,
            account_login=info.account_login,
            status=status,
            github_account_id=info.account_id,
        )
        if updated != current:
            uow.tenant_admin.update_organization(updated, uninstalled_at=None)
            if updated.status is not current.status:
                self._audit(uow, current.id, _ACTION_FOR_STATUS[status], now)
        return updated

    def _register(
        self, uow: UnitOfWork, info: InstallationInfo, status: OrganizationStatus, now: datetime
    ) -> tuple[Organization, SyncOutcome]:
        previous = uow.tenant_admin.find_organization_by_account(info.account_id)
        if previous is not None:
            # Reinstalación: nueva instalación de la misma cuenta, conserva su historial.
            org = replace(
                previous,
                github_installation_id=info.installation_id,
                account_login=info.account_login,
                status=status,
            )
            uow.tenant_admin.update_organization(org, uninstalled_at=None)
            self._audit(uow, org.id, "organization.reactivated", now)
            return org, SyncOutcome.REGISTERED
        org = Organization(
            id=OrganizationId(self._ids.new_uuid()),
            github_installation_id=info.installation_id,
            account_login=info.account_login,
            status=status,
            github_account_id=info.account_id,
        )
        uow.tenant_admin.add_organization(org)
        self._audit(uow, org.id, "organization.registered", now)
        # Solo en el alta: una reinstalación conserva la política que dejó su ADMIN.
        version = uow.policies.publish(
            org.id, default_policy(), policy_id=self._ids.new_uuid(), at=now
        )
        self._audit(
            uow, org.id, "policy.defaulted", now, target_type="policy", target_id=str(version)
        )
        return org, SyncOutcome.REGISTERED

    def _sync_repositories(
        self,
        uow: UnitOfWork,
        org: Organization,
        remote: Sequence[RemoteRepository],
        now: datetime,
    ) -> None:
        admin = uow.tenant_admin
        known = {r.github_repo_id: r for r in admin.list_repositories(org.id)}
        seen: set[int] = set()
        for item in remote:
            if item.github_repo_id in seen:
                continue
            seen.add(item.github_repo_id)
            existing = known.get(item.github_repo_id)
            if existing is not None:
                changed = replace(existing, ref=item.ref, default_branch=item.default_branch)
                if changed != existing:
                    admin.update_repository(changed)
                continue
            if uow.tenants.find_repository_by_github_id(item.github_repo_id) is not None:
                # Ya pertenece a otra organización (p. ej. transferido): no se mueve entre
                # inquilinos sin una decisión humana.
                self._audit(
                    uow,
                    org.id,
                    "repository.conflict",
                    now,
                    target_type="repository",
                    target_id=str(item.github_repo_id),
                )
                continue
            admin.add_repository(
                Repository(
                    id=RepositoryId(self._ids.new_uuid()),
                    organization_id=org.id,
                    github_repo_id=item.github_repo_id,
                    ref=item.ref,
                    default_branch=item.default_branch,
                    enabled=True,
                )
            )
        for repo_id, existing in known.items():
            if repo_id not in seen and existing.enabled:
                admin.update_repository(replace(existing, enabled=False))

    @staticmethod
    def _audit(  # noqa: PLR0913 - campos del registro de auditoría
        uow: UnitOfWork,
        organization_id: OrganizationId,
        action: str,
        at: datetime,
        *,
        reason: str | None = None,
        target_type: str = "organization",
        target_id: str | None = None,
    ) -> None:
        uow.audit.append(
            AuditEntry(
                occurred_at=at,
                action=action,
                actor=None,
                organization_id=organization_id,
                target_type=target_type,
                target_id=target_id or str(organization_id.value),
                details={"reason": reason} if reason else {},
            )
        )
