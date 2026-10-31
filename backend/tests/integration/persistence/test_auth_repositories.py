"""Usuarios, sesiones y flujos de login sobre PostgreSQL real (ADR 0016)."""

import threading
from uuid import UUID

import pytest
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from nave.application.dto.auth import LoginFlow, OidcIdentity, SessionRecord
from nave.application.errors import AuthenticationError, NotFoundError
from nave.application.services.session_tokens import hash_token
from nave.application.use_cases.bootstrap_access import BootstrapAccess
from nave.domain.value_objects.identifiers import UserId
from nave.domain.value_objects.role import Role
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.fakes.auth import make_auth
from tests.fakes.memory import FakeClock, Store
from tests.integration.persistence.seed import ORG, at, seed_tenancy

pytestmark = pytest.mark.integration

ALICE = OidcIdentity(
    issuer="https://idp.example", subject="alice", display_name="Alice", email="a@example.com"
)


@pytest.fixture
def uow(engine: Engine) -> SqlUnitOfWorkFactory:
    return SqlUnitOfWorkFactory(engine)


def flow(n: int, *, minutes: int = 10) -> LoginFlow:
    return LoginFlow(
        state_hash=bytes([n]) * 32,
        binding_hash=bytes([n + 100]) * 32,
        nonce=f"nonce-{n}",
        code_verifier=f"verifier-{n}",
        created_at=at(0),
        expires_at=at(minutes),
    )


def session(n: int, user: UserId, *, minutes: int = 60) -> SessionRecord:
    return SessionRecord(
        token_hash=bytes([n]) * 32,
        user_id=user,
        csrf_token=f"csrf-{n}",
        created_at=at(0),
        last_seen_at=at(0),
        expires_at=at(minutes),
    )


class TestUsers:
    def test_record_login_is_an_upsert_on_issuer_and_subject(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        with uow() as tx:
            first = tx.users.record_login(ALICE, new_id=UUID(int=1), at=at(1))
            tx.commit()
        renamed = OidcIdentity(issuer=ALICE.issuer, subject="alice", display_name="Alice B")
        with uow() as tx:
            again = tx.users.record_login(renamed, new_id=UUID(int=2), at=at(5))
            tx.commit()
        assert first == again == UserId(UUID(int=1))
        with uow() as tx:
            user = tx.users.get(first)
        assert (user.display_name, user.email, user.last_login_at) == ("Alice B", None, at(5))

    def test_same_subject_at_another_issuer_is_another_user(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        other = OidcIdentity(issuer="https://other.example", subject="alice")
        with uow() as tx:
            a = tx.users.record_login(ALICE, new_id=UUID(int=1), at=at(1))
            b = tx.users.record_login(other, new_id=UUID(int=2), at=at(1))
            tx.commit()
        assert a != b

    def test_unknown_user(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx, pytest.raises(NotFoundError):
            tx.users.get(UserId(UUID(int=77)))

    def test_list_recent_orders_by_last_login_with_never_logged_in_last(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        seed_tenancy(engine)  # dos usuarios sin último acceso
        with uow() as tx:
            tx.users.record_login(ALICE, new_id=UUID(int=1), at=at(1))
            bob = OidcIdentity(issuer=ALICE.issuer, subject="bob")
            tx.users.record_login(bob, new_id=UUID(int=2), at=at(9))
            tx.commit()
        with uow() as tx:
            listed = tx.users.list_recent(limit=10)
        assert [u.subject for u in listed][:2] == ["bob", "alice"]
        assert len(listed) == 4
        with uow() as tx:
            assert len(tx.users.list_recent(limit=1)) == 1


class TestLoginFlows:
    def test_consume_returns_the_flow_once(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            tx.login_flows.add(flow(1))
            tx.commit()
        with uow() as tx:
            assert tx.login_flows.consume(flow(1).state_hash, at(1)) == flow(1)
            tx.commit()
        with uow() as tx:
            assert tx.login_flows.consume(flow(1).state_hash, at(1)) is None

    def test_an_expired_flow_is_deleted_but_not_returned(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            tx.login_flows.add(flow(1))
            tx.commit()
        with uow() as tx:
            assert tx.login_flows.consume(flow(1).state_hash, at(11)) is None
            tx.commit()
        with uow() as tx:
            assert tx.login_flows.count_live(at(0)) == 0

    def test_concurrent_callbacks_with_the_same_state_cannot_both_win(
        self, uow: SqlUnitOfWorkFactory
    ) -> None:
        with uow() as tx:
            tx.login_flows.add(flow(1))
            tx.commit()
        winners: list[LoginFlow] = []
        gate = threading.Barrier(8)

        def attempt() -> None:
            gate.wait()
            with uow() as tx:
                got = tx.login_flows.consume(flow(1).state_hash, at(1))
                tx.commit()
            if got is not None:
                winners.append(got)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(winners) == 1

    def test_cleanup_is_bounded_and_counts_only_live_flows(self, uow: SqlUnitOfWorkFactory) -> None:
        with uow() as tx:
            for n in range(1, 6):
                tx.login_flows.add(flow(n, minutes=n))
            tx.commit()
        with uow() as tx:
            assert tx.login_flows.count_live(at(0)) == 5
            assert tx.login_flows.count_live(at(3)) == 2
            assert tx.login_flows.delete_expired(at(4), limit=2) == 2
            assert tx.login_flows.delete_expired(at(4), limit=100) == 2
            tx.commit()
        with uow() as tx:
            assert tx.login_flows.count_live(at(0)) == 1


class TestSessions:
    @pytest.fixture
    def user(self, uow: SqlUnitOfWorkFactory) -> UserId:
        with uow() as tx:
            user_id = tx.users.record_login(ALICE, new_id=UUID(int=1), at=at(0))
            tx.commit()
        return user_id

    def test_roundtrip_touch_delete(self, uow: SqlUnitOfWorkFactory, user: UserId) -> None:
        with uow() as tx:
            tx.sessions.add(session(1, user))
            tx.commit()
        with uow() as tx:
            assert tx.sessions.get(session(1, user).token_hash) == session(1, user)
            tx.sessions.touch(session(1, user).token_hash, at(7))
            tx.commit()
        with uow() as tx:
            got = tx.sessions.get(session(1, user).token_hash)
            assert got is not None
            assert got.last_seen_at == at(7)
            assert tx.sessions.delete(session(1, user).token_hash) is True
            assert tx.sessions.delete(session(1, user).token_hash) is False
            assert tx.sessions.get(session(1, user).token_hash) is None

    def test_cleanup_removes_only_expired_sessions_in_bounded_batches(
        self, uow: SqlUnitOfWorkFactory, user: UserId
    ) -> None:
        with uow() as tx:
            for n in range(1, 5):
                tx.sessions.add(session(n, user, minutes=n * 10))
            tx.commit()
        with uow() as tx:
            assert tx.sessions.delete_expired(at(25), limit=1) == 1
            assert tx.sessions.delete_expired(at(25), limit=10) == 1
            assert tx.sessions.get(session(3, user).token_hash) is not None
            tx.commit()

    def test_database_rejects_hashes_of_the_wrong_length(
        self, engine: Engine, user: UserId
    ) -> None:
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                insert(t.sessions).values(
                    token_hash=b"short",
                    user_id=user.value,
                    csrf_token="x",
                    created_at=at(0),
                    last_seen_at=at(0),
                    expires_at=at(1),
                )
            )


class TestFullFlowOnPostgres:
    def test_login_authenticate_logout(self, uow: SqlUnitOfWorkFactory, engine: Engine) -> None:
        clock = FakeClock(at(0))
        kit = make_auth(Store(), clock, uow_factory=uow)
        started = kit.begin.execute()
        done = kit.complete.execute(
            state=kit.oidc.urls[0]["state"], code="good", binding=started.binding
        )
        principal = kit.authenticate.execute(done.token)
        assert principal.display_name == "Alice"
        with pytest.raises(AuthenticationError):
            kit.complete.execute(
                state=kit.oidc.urls[0]["state"], code="good", binding=started.binding
            )
        with engine.connect() as conn:
            stored: bytes = conn.execute(select(t.sessions.c.token_hash)).scalar_one()
            assert bytes(stored) == hash_token(done.token)
            dump = "".join(str(r) for r in conn.execute(text("SELECT * FROM sessions")).all())
            assert done.token not in dump
        clock.advance(minutes=31)
        with pytest.raises(AuthenticationError):
            kit.authenticate.execute(done.token)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0

    def test_first_admin_through_the_cli_use_case(
        self, uow: SqlUnitOfWorkFactory, engine: Engine
    ) -> None:
        seed_tenancy(engine)
        clock = FakeClock(at(0))
        kit = make_auth(Store(), clock, uow_factory=uow)
        started = kit.begin.execute()
        kit.complete.execute(state=kit.oidc.urls[0]["state"], code="good", binding=started.binding)
        access = BootstrapAccess(uow_factory=uow, clock=clock)
        (newest, *_) = access.list_users(limit=5)
        access.grant_admin(organization_login="acme", user=newest.id)
        with uow() as tx:
            assert tx.memberships.role_of(newest.id, ORG) is Role.ADMIN
            assert tx.memberships.count_admins(ORG) == 1
        with engine.connect() as conn:
            granted_by: object = conn.execute(
                select(t.memberships.c.granted_by).where(t.memberships.c.user_id == newest.id.value)
            ).scalar_one()
            assert granted_by is None
            action: str = conn.execute(
                select(t.audit_log.c.action).order_by(t.audit_log.c.id.desc()).limit(1)
            ).scalar_one()
            assert action == "membership.bootstrapped"


class TestPrivileges:
    @pytest.fixture(autouse=True)
    def _seed(self, engine: Engine) -> None:
        seed_tenancy(engine)

    def test_the_app_role_can_manage_sessions_and_flows_and_update_users(
        self, engine: Engine
    ) -> None:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE nave_app"))
            conn.execute(text("UPDATE users SET display_name = 'x'"))
            conn.execute(text("DELETE FROM login_flows"))
            conn.execute(text("DELETE FROM sessions"))
            conn.execute(text("SELECT count(*) FROM sessions"))

    @pytest.mark.parametrize(
        ("role", "statement"),
        [
            ("nave_app", "DELETE FROM users"),
            ("nave_app", "TRUNCATE sessions"),
            ("nave_app", "ALTER TABLE sessions ADD COLUMN x int"),
            ("nave_maintenance", "SELECT * FROM sessions"),
            ("nave_maintenance", "SELECT * FROM login_flows"),
            ("nave_maintenance", "SELECT oidc_subject FROM users"),
        ],
    )
    def test_nobody_else_touches_the_authentication_tables(
        self, engine: Engine, role: str, statement: str
    ) -> None:
        with (
            pytest.raises(ProgrammingError, match=r"permission denied|must be owner"),
            engine.begin() as conn,
        ):
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            conn.execute(text(statement))

    def test_users_cannot_be_deleted_while_referenced(self, engine: Engine) -> None:
        with pytest.raises(DBAPIError), engine.begin() as conn:
            conn.execute(
                insert(t.memberships).values(
                    user_id=UUID(int=0xA1),
                    organization_id=ORG.value,
                    role="ADMIN",
                    granted_at=at(0),
                )
            )
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": UUID(int=0xA1)})
