"""Administración de plataforma en PostgreSQL (ADR 0030)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final

from sqlalchemy import Connection, Engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nave.application.dto.platform import (
    ComponentHealth,
    CounterBucket,
    DeliveryRecord,
    HealthComponent,
    HealthStatus,
    PlatformOrganization,
    PlatformUser,
    UserMembership,
    WebhookReason,
)
from nave.domain.entities.tenancy import OrganizationStatus
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.errors import translated_errors

_COMPONENTS: Final = {c.value: c for c in HealthComponent}
MAX_ORGANIZATIONS: Final = 1000


class SqlPlatformAdminRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def is_admin(self, user: UserId) -> bool:
        p = t.platform_admins
        return (
            self._conn.execute(select(p.c.user_id).where(p.c.user_id == user.value)).first()
            is not None
        )

    def list_admins(self) -> Sequence[UserId]:
        p = t.platform_admins
        rows = self._conn.execute(select(p.c.user_id).order_by(p.c.granted_at, p.c.user_id))
        return [UserId(row.user_id) for row in rows]

    def grant(self, user: UserId, at: datetime) -> bool:
        statement = (
            pg_insert(t.platform_admins)
            .values(user_id=user.value, granted_at=at)
            .on_conflict_do_nothing(index_elements=["user_id"])
            .returning(t.platform_admins.c.user_id)
        )
        # Con ON CONFLICT DO NOTHING, `rowcount` no es fiable en todos los drivers.
        return self._conn.execute(statement).first() is not None

    def revoke(self, user: UserId) -> bool:
        p = t.platform_admins
        return self._conn.execute(delete(p).where(p.c.user_id == user.value)).rowcount > 0


class SqlPlatformQueries:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def users(self, *, limit: int, now: datetime) -> Sequence[PlatformUser]:
        u, s, p = t.users, t.sessions, t.platform_admins
        live = (
            select(func.count())
            .select_from(s)
            .where(s.c.user_id == u.c.id, s.c.expires_at > now)
            .scalar_subquery()
        )
        admin = select(p.c.user_id).where(p.c.user_id == u.c.id).exists()
        rows = self._conn.execute(
            select(u, live.label("live"), admin.label("platform_admin"))
            .order_by(u.c.last_login_at.desc().nulls_last(), u.c.id)
            .limit(limit)
        ).all()
        memberships = self._memberships([row.id for row in rows])
        return [
            PlatformUser(
                id=UserId(row.id),
                display_name=row.display_name,
                email=row.email,
                last_login_at=row.last_login_at,
                live_sessions=int(row.live),
                platform_admin=bool(row.platform_admin),
                memberships=tuple(memberships.get(row.id, ())),
            )
            for row in rows
        ]

    def _memberships(self, user_ids: Sequence[object]) -> dict[object, list[UserMembership]]:
        if not user_ids:
            return {}
        m, o = t.memberships, t.organizations
        rows = self._conn.execute(
            select(m.c.user_id, m.c.role, o.c.id, o.c.account_login)
            .join(o, o.c.id == m.c.organization_id)
            .where(m.c.user_id.in_(user_ids), o.c.status != OrganizationStatus.UNINSTALLED.value)
            .order_by(o.c.account_login, o.c.id)
        )
        found: dict[object, list[UserMembership]] = {}
        for row in rows:
            found.setdefault(row.user_id, []).append(
                UserMembership(OrganizationId(row.id), row.account_login, Role.parse(row.role))
            )
        return found

    def organizations(self) -> Sequence[PlatformOrganization]:
        o, m = t.organizations, t.memberships
        members = (
            select(func.count())
            .select_from(m)
            .where(m.c.organization_id == o.c.id)
            .scalar_subquery()
        )
        admins = (
            select(func.count())
            .select_from(m)
            .where(m.c.organization_id == o.c.id, m.c.role == Role.ADMIN.value)
            .scalar_subquery()
        )
        rows = self._conn.execute(
            select(o.c.id, o.c.account_login, o.c.status, members.label("n"), admins.label("a"))
            .where(o.c.status != OrganizationStatus.UNINSTALLED.value)
            .order_by(o.c.account_login, o.c.id)
            .limit(MAX_ORGANIZATIONS)
        )
        return [
            PlatformOrganization(
                OrganizationId(row.id),
                row.account_login,
                OrganizationStatus(row.status),
                int(row.n),
                int(row.a),
            )
            for row in rows
        ]

    def recent_deliveries(self, *, limit: int) -> Sequence[DeliveryRecord]:
        w = t.webhook_deliveries
        rows = self._conn.execute(
            select(w).order_by(w.c.received_at.desc(), w.c.delivery_id).limit(limit)
        )
        return [
            DeliveryRecord(str(row.delivery_id), row.event, row.outcome, row.received_at)
            for row in rows
        ]

    def delivery_outcomes(self, *, since: datetime) -> Mapping[str, int]:
        w = t.webhook_deliveries
        rows = self._conn.execute(
            select(w.c.outcome, func.count().label("n"))
            .where(w.c.received_at >= since)
            .group_by(w.c.outcome)
        )
        return {row.outcome: int(row.n) for row in rows}

    def webhook_counters(self, *, since: datetime) -> Sequence[CounterBucket]:
        c = t.webhook_counters
        rows = self._conn.execute(
            select(c).where(c.c.minute >= since).order_by(c.c.minute, c.c.reason).limit(10_000)
        )
        return [
            # `row.count` es el método de la tupla, no la columna.
            CounterBucket(row.minute, WebhookReason(row.reason), int(row._mapping["count"]))
            for row in rows
        ]

    def health_reports(self) -> Sequence[ComponentHealth]:
        h = t.health_reports
        found = []
        for row in self._conn.execute(select(h)):
            component = _COMPONENTS.get(row.component)
            if component is None:
                continue  # fila de una versión anterior: se ignora
            found.append(
                ComponentHealth.of(
                    component, HealthStatus(row.status), row.detail, row.checked_at, row.latency_ms
                )
            )
        return found


class SqlHealthStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def save(self, reports: Sequence[ComponentHealth]) -> None:
        for report in reports:
            statement = pg_insert(t.health_reports).values(
                component=report.component.value,
                status=report.status.value,
                detail=report.detail,
                latency_ms=report.latency_ms,
                checked_at=report.checked_at,
            )
            self._conn.execute(
                statement.on_conflict_do_update(
                    index_elements=["component"],
                    set_={
                        "status": statement.excluded.status,
                        "detail": statement.excluded.detail,
                        "latency_ms": statement.excluded.latency_ms,
                        "checked_at": statement.excluded.checked_at,
                    },
                )
            )


class SqlWebhookCounterStore:
    """Fuera de una unidad de trabajo: lo usa el acumulador de la API en su volcado."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, counts: Mapping[tuple[datetime, WebhookReason], int]) -> None:
        if not counts:
            return
        c = t.webhook_counters
        with translated_errors(), self._engine.begin() as conn:
            for (minute, reason), n in sorted(
                counts.items(), key=lambda i: (i[0][0], i[0][1].value)
            ):
                statement = pg_insert(c).values(minute=minute, reason=reason.value, count=n)
                conn.execute(
                    statement.on_conflict_do_update(
                        index_elements=["minute", "reason"],
                        set_={"count": c.c.count + statement.excluded.count},
                    )
                )
