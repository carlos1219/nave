"""Administración de plataforma (ADR 0030): usuarios, salud y webhooks de toda la instalación.

Todos exigen que el actor sea administrador de plataforma. Quien no lo es recibe la respuesta
de «no existe», como un no miembro de una organización (ADR 0026)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from nave.application.dto.audit import AuditEntry
from nave.application.dto.platform import (
    ComponentHealth,
    HealthComponent,
    HealthReport,
    HealthStatus,
    PlatformOrganization,
    PlatformUser,
    WebhookActivity,
    WebhookReason,
)
from nave.application.errors import AccessDeniedError, NotFoundError
from nave.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from nave.application.ports.platform import HealthProbesPort, WebhookStatsPort
from nave.application.ports.system import Clock
from nave.application.use_cases.manage_membership import change_membership
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role

_LOG: Final = logging.getLogger("nave.authz")
MAX_USERS: Final = 500
MAX_DELIVERIES: Final = 200
WEBHOOK_WINDOW: Final = timedelta(hours=24)
HEARTBEAT_STALE: Final = timedelta(minutes=3)
# Lo que comprueba el worker; si su latido es viejo, ninguno de estos datos vale.
_FROM_WORKER: Final = frozenset(
    {
        HealthComponent.SCAN_RUNNER,
        HealthComponent.EGRESS_PROXY,
        HealthComponent.WORKER,
    }
)


class PlatformAuthorizer:
    def require(self, uow: UnitOfWork, actor: UserId, target: str) -> None:
        if uow.platform_admins.is_admin(actor):
            return
        _LOG.warning(
            "access denied to a non platform admin",
            extra={"action": "access.denied", "user_id": str(actor), "target_type": target},
        )
        raise NotFoundError(target)


class _PlatformUseCase:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._authorizer = PlatformAuthorizer()


class IsPlatformAdmin(_PlatformUseCase):
    """Solo para mostrar u ocultar la sección en la interfaz; no autoriza nada."""

    def execute(self, actor: UserId) -> bool:
        with self._uow_factory() as uow:
            return uow.platform_admins.is_admin(actor)


class ListPlatformUsers(_PlatformUseCase):
    def execute(self, actor: UserId) -> Sequence[PlatformUser]:
        with self._uow_factory() as uow:
            self._authorizer.require(uow, actor, "platform")
            return uow.platform.users(limit=MAX_USERS, now=self._clock.now())


class ListPlatformOrganizations(_PlatformUseCase):
    def execute(self, actor: UserId) -> Sequence[PlatformOrganization]:
        with self._uow_factory() as uow:
            self._authorizer.require(uow, actor, "platform")
            return uow.platform.organizations()


@dataclass(frozen=True, slots=True, kw_only=True)
class PlatformMembershipCommand:
    actor: UserId
    user: UserId
    organization_id: OrganizationId
    role: Role | None


class SetMembershipAsPlatform(_PlatformUseCase):
    """Mismas reglas que un ADMIN de la organización; queda en su auditoría con
    `via=platform`.

    Nunca sobre la pertenencia del propio actor: el rol de plataforma no da acceso a los
    datos de ninguna organización, y sin esta regla bastaría con darse ADMIN a uno mismo."""

    def execute(self, command: PlatformMembershipCommand) -> None:
        with self._uow_factory() as uow:
            self._authorizer.require(uow, command.actor, "platform")
            uow.tenants.get_organization(command.organization_id)
            if command.user == command.actor:
                uow.audit.append(
                    AuditEntry(
                        occurred_at=self._clock.now(),
                        action="access.denied",
                        actor=command.actor,
                        organization_id=command.organization_id,
                        target_type="membership",
                        target_id=str(command.actor),
                        details={"via": "platform", "reason": "self"},
                    )
                )
                uow.commit()
                raise AccessDeniedError("platform admins cannot change their own memberships")
            changed = change_membership(
                uow,
                actor=command.actor,
                organization_id=command.organization_id,
                user=command.user,
                role=command.role,
                at=self._clock.now(),
                via="platform",
            )
            if changed:
                uow.commit()


@dataclass(frozen=True, slots=True, kw_only=True)
class RevokeUserSessionsCommand:
    actor: UserId
    user: UserId


class RevokeUserSessions(_PlatformUseCase):
    def execute(self, command: RevokeUserSessionsCommand) -> int:
        with self._uow_factory() as uow:
            self._authorizer.require(uow, command.actor, "platform")
            uow.users.get(command.user)
            revoked = uow.sessions.delete_for_user(command.user)
            uow.audit.append(
                AuditEntry(
                    occurred_at=self._clock.now(),
                    action="session.revoked_by_platform",
                    actor=command.actor,
                    organization_id=None,
                    target_type="user",
                    target_id=str(command.user),
                    details={"sessions": str(revoked)},
                )
            )
            uow.commit()
        return revoked


class GetPlatformHealth(_PlatformUseCase):
    """Lo que la API ve en vivo (base de datos, broker, colas) más el último latido del
    worker, que comprueba lo que la API no alcanza (scan-runner, proxy de salida)."""

    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, probes: HealthProbesPort
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock)
        self._probes = probes

    def execute(self, actor: UserId) -> HealthReport:
        with self._uow_factory() as uow:
            self._authorizer.require(uow, actor, "platform")
            stored = list(uow.platform.health_reports())
        now = self._clock.now()
        live = {r.component: r for r in self._probes.probe()}
        beat = next((r for r in stored if r.component is HealthComponent.WORKER), None)
        heartbeat_at = beat.checked_at if beat else None
        stale = heartbeat_at is None or now - heartbeat_at > HEARTBEAT_STALE
        merged: dict[HealthComponent, ComponentHealth] = {r.component: r for r in stored}
        merged.update(live)
        if stale:
            for component in _FROM_WORKER:
                merged[component] = ComponentHealth.of(
                    component,
                    HealthStatus.DOWN,
                    "Sin latido reciente del worker: worker o scheduler parados",
                    heartbeat_at or now,
                )
        order = list(HealthComponent)
        return HealthReport(
            components=tuple(sorted(merged.values(), key=lambda r: order.index(r.component))),
            heartbeat_at=heartbeat_at,
            stale=stale,
        )


class GetWebhookActivity(_PlatformUseCase):
    def execute(self, actor: UserId) -> WebhookActivity:
        since = self._clock.now() - WEBHOOK_WINDOW
        with self._uow_factory() as uow:
            self._authorizer.require(uow, actor, "platform")
            return WebhookActivity(
                recent=tuple(uow.platform.recent_deliveries(limit=MAX_DELIVERIES)),
                outcomes=tuple(sorted(uow.platform.delivery_outcomes(since=since).items())),
                counters=tuple(uow.platform.webhook_counters(since=since)),
            )


class RecordWebhookEvent:
    """Rechazo o duplicado de un webhook. El puerto acumula y vuelca con intervalo mínimo:
    un rechazo por firma ocurre antes de autenticar y no puede costar una escritura."""

    def __init__(self, *, stats: WebhookStatsPort) -> None:
        self._stats = stats

    def execute(self, reason: WebhookReason) -> None:
        self._stats.count(reason)


class RecordHealth:
    """Tarea periódica del worker: comprueba lo que ve y guarda el resultado (latido)."""

    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, probes: HealthProbesPort
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._probes = probes

    def execute(self) -> Sequence[ComponentHealth]:
        reports = [
            *self._probes.probe(),
            ComponentHealth.of(
                HealthComponent.WORKER, HealthStatus.OK, "Latido del worker", self._clock.now()
            ),
        ]
        with self._uow_factory() as uow:
            uow.health.save(reports)
            uow.commit()
        return reports
