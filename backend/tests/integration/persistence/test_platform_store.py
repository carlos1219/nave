"""ADR 0030 sobre PostgreSQL real: administradores de plataforma, usuarios, salud, contadores
de webhooks, versiones de política y su retención."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, insert, select

from nave.application.dto.auth import OidcIdentity, SessionRecord
from nave.application.dto.platform import (
    ComponentHealth,
    HealthComponent,
    HealthStatus,
    WebhookReason,
)
from nave.domain.quality_gates import Gate, GateScope, QualityGatePolicy
from nave.domain.value_objects.identifiers import UserId
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.severity import Severity
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.maintenance import SqlRetentionStore
from nave.infrastructure.persistence.platform import SqlWebhookCounterStore
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.integration.persistence.seed import ORG, at, seed_tenancy

pytestmark = pytest.mark.integration
IDENTITY = OidcIdentity(issuer="https://idp.test", subject="carol", display_name="Carol")


@pytest.fixture
def uow(engine: Engine) -> SqlUnitOfWorkFactory:
    seed_tenancy(engine)
    return SqlUnitOfWorkFactory(engine)


def new_user(uow: SqlUnitOfWorkFactory) -> UserId:
    with uow() as tx:
        user = tx.users.record_login(IDENTITY, new_id=UUID(int=0xCA), at=at(0))
        tx.commit()
    return user


class TestPlatformAdmins:
    def test_grant_and_revoke_are_idempotent(self, uow: SqlUnitOfWorkFactory) -> None:
        user = new_user(uow)
        with uow() as tx:
            assert tx.platform_admins.grant(user, at(1)) is True
            assert tx.platform_admins.grant(user, at(2)) is False
            tx.commit()
        with uow() as tx:
            assert tx.platform_admins.is_admin(user) is True
            assert tx.platform_admins.list_admins() == [user]
            assert tx.platform_admins.revoke(user) is True
            assert tx.platform_admins.revoke(user) is False


class TestUsersAndOrganizations:
    def test_users_with_memberships_live_sessions_and_role(self, uow: SqlUnitOfWorkFactory) -> None:
        user = new_user(uow)
        with uow() as tx:
            tx.memberships.set_role(user, ORG, Role.TRIAGER, granted_by=None, at=at(1))
            for n, expires in ((1, at(600)), (2, at(-600))):
                tx.sessions.add(
                    SessionRecord(
                        token_hash=bytes([n]) * 32,
                        user_id=user,
                        csrf_token="c",
                        created_at=at(-700),
                        last_seen_at=at(-700),
                        expires_at=expires,
                    )
                )
            tx.platform_admins.grant(user, at(1))
            tx.commit()
        with uow() as tx:
            found = next(u for u in tx.platform.users(limit=10, now=at(0)) if u.id == user)
            (org,) = tx.platform.organizations()
            assert tx.sessions.delete_for_user(user) == 2
        assert found.live_sessions == 1
        assert found.platform_admin is True
        assert [(m.login, m.role) for m in found.memberships] == [("acme", Role.TRIAGER)]
        assert org.login == "acme"
        assert org.members >= 1
        assert 0 <= org.admins <= org.members


class TestHealthAndWebhooks:
    def test_health_reports_are_replaced_per_component(self, uow: SqlUnitOfWorkFactory) -> None:
        for minute, status in ((1, HealthStatus.DOWN), (2, HealthStatus.OK)):
            with uow() as tx:
                tx.health.save(
                    [ComponentHealth.of(HealthComponent.SCAN_RUNNER, status, "x", at(minute), 7)]
                )
                tx.commit()
        with uow() as tx:
            (report,) = tx.platform.health_reports()
        assert (report.status, report.checked_at, report.latency_ms) == (HealthStatus.OK, at(2), 7)

    def test_counters_add_up_and_the_retention_purges_old_minutes(self, engine: Engine) -> None:
        store = SqlWebhookCounterStore(engine)
        minute = at(0).replace(second=0, microsecond=0)
        store.add({(minute, WebhookReason.SIGNATURE): 3})
        store.add({(minute, WebhookReason.SIGNATURE): 2, (minute, WebhookReason.DUPLICATE): 1})
        old = minute - timedelta(days=40)
        store.add({(old, WebhookReason.MALFORMED): 5})
        uow = SqlUnitOfWorkFactory(engine)
        with uow() as tx:
            buckets = tx.platform.webhook_counters(since=minute - timedelta(days=1))
        assert [(b.reason, b.count) for b in buckets] == [
            (WebhookReason.DUPLICATE, 1),
            (WebhookReason.SIGNATURE, 5),
        ]
        assert (
            SqlRetentionStore(engine).purge_webhook_counters(minute - timedelta(days=30), 100) == 1
        )
        with engine.connect() as conn:
            assert len(conn.execute(select(t.webhook_counters)).all()) == 2

    def test_recent_deliveries_and_outcomes(self, engine: Engine) -> None:
        with engine.begin() as conn:
            for n, outcome in ((1, "ENQUEUED"), (2, "IGNORED"), (3, "ENQUEUED")):
                conn.execute(
                    insert(t.webhook_deliveries).values(
                        delivery_id=UUID(int=n), event="push", received_at=at(n), outcome=outcome
                    )
                )
        with SqlUnitOfWorkFactory(engine)() as tx:
            recent = tx.platform.recent_deliveries(limit=2)
            outcomes = tx.platform.delivery_outcomes(since=at(2))
        assert [d.received_at for d in recent] == [at(3), at(2)]
        assert outcomes == {"ENQUEUED": 1, "IGNORED": 1}


class TestPolicyVersions:
    def test_publish_adds_a_new_active_version_and_keeps_the_old_one(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        policy = QualityGatePolicy(
            version=1,
            gates=(Gate("no-new-high", GateScope.NEW_FINDINGS, {Severity.HIGH: 0}),),
            sla_days={Severity.CRITICAL: 7},
            fail_on_sla_breach=False,
        )
        for n in (1, 2):
            with uow() as tx:
                assert tx.policies.publish(ORG, policy, policy_id=UUID(int=0xD0 + n), at=at(n)) == n
                tx.commit()
        with uow() as tx:
            versions = tx.insights.policy_versions(ORG)
            active = tx.policies.active_policy(ORG)
        assert [(v.version, v.active) for v in versions] == [(2, True), (1, False)]
        assert active is not None
        assert active.version == 2
        assert active.policy == policy
