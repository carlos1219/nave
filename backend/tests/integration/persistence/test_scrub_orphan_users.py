"""Minimización de datos: la función SQL vacía nombre y correo de quien ya no tiene acceso."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.exc import ProgrammingError

from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.maintenance import SqlOrphanedUserStore
from tests.integration.persistence.seed import ORG, seed_tenancy

pytestmark = pytest.mark.integration

OLD_ORPHAN = UUID(int=0xE1)
RECENT_ORPHAN = UUID(int=0xE2)
OLD_MEMBER = UUID(int=0xE3)
OLD_WITH_LIVE_SESSION = UUID(int=0xE4)
OLD_WITH_EXPIRED_SESSION = UUID(int=0xE5)
NEVER_LOGGED = UUID(int=0xE6)


def days_ago(n: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


def populate(engine: Engine) -> None:
    seed_tenancy(engine)
    users = [
        (OLD_ORPHAN, 40),
        (RECENT_ORPHAN, 5),
        (OLD_MEMBER, 40),
        (OLD_WITH_LIVE_SESSION, 40),
        (OLD_WITH_EXPIRED_SESSION, 40),
        (NEVER_LOGGED, None),
    ]
    with engine.begin() as conn:
        for user_id, age in users:
            conn.execute(
                insert(t.users).values(
                    id=user_id,
                    oidc_issuer="https://idp.test",
                    oidc_subject=f"sub-{user_id.int}",
                    display_name=f"Name {user_id.int}",
                    email=f"u{user_id.int}@example.com",
                    last_login_at=None if age is None else days_ago(age),
                )
            )
        conn.execute(
            insert(t.memberships).values(
                user_id=OLD_MEMBER,
                organization_id=ORG.value,
                role="VIEWER",
                granted_at=days_ago(50),
            )
        )
        for user_id, expires in (
            (OLD_WITH_LIVE_SESSION, days_ago(-1)),
            (OLD_WITH_EXPIRED_SESSION, days_ago(1)),
        ):
            conn.execute(
                insert(t.sessions).values(
                    token_hash=user_id.bytes + user_id.bytes,
                    user_id=user_id,
                    csrf_token="c" * 43,
                    created_at=days_ago(2),
                    last_seen_at=days_ago(2),
                    expires_at=expires,
                )
            )


def profile(engine: Engine, user_id: UUID) -> tuple[str | None, str | None]:
    with engine.connect() as conn:
        row = conn.execute(
            select(t.users.c.display_name, t.users.c.email).where(t.users.c.id == user_id)
        ).one()
    return row.display_name, row.email


def scrubbed(engine: Engine) -> set[UUID]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(t.users.c.id).where(t.users.c.display_name.is_(None), t.users.c.email.is_(None))
        ).all()
    return {r.id for r in rows} & {
        OLD_ORPHAN,
        RECENT_ORPHAN,
        OLD_MEMBER,
        OLD_WITH_LIVE_SESSION,
        OLD_WITH_EXPIRED_SESSION,
        NEVER_LOGGED,
    }


def test_only_idle_users_without_membership_or_live_session_are_scrubbed(engine: Engine) -> None:
    populate(engine)
    store = SqlOrphanedUserStore(engine)
    assert store.scrub_batch(days_ago(30), 100) == 3
    assert scrubbed(engine) == {OLD_ORPHAN, OLD_WITH_EXPIRED_SESSION, NEVER_LOGGED}
    assert profile(engine, OLD_MEMBER)[0] is not None
    assert profile(engine, OLD_WITH_LIVE_SESSION)[1] is not None
    assert profile(engine, RECENT_ORPHAN)[0] is not None


def test_the_row_and_identity_survive_so_audit_references_stay_valid(engine: Engine) -> None:
    populate(engine)
    SqlOrphanedUserStore(engine).scrub_batch(days_ago(30), 100)
    with engine.connect() as conn:
        row = conn.execute(select(t.users).where(t.users.c.id == OLD_ORPHAN)).one()
    assert (row.oidc_issuer, row.oidc_subject) == ("https://idp.test", f"sub-{OLD_ORPHAN.int}")


def test_it_is_idempotent_and_respects_the_batch_limit(engine: Engine) -> None:
    populate(engine)
    store = SqlOrphanedUserStore(engine)
    assert [store.scrub_batch(days_ago(30), 1) for _ in range(4)] == [1, 1, 1, 0]


def test_the_database_enforces_the_minimum_grace_period(engine: Engine) -> None:
    populate(engine)
    # Aunque el llamante pida «hasta ahora mismo», la función exige 30 días de inactividad.
    assert SqlOrphanedUserStore(engine).scrub_batch(datetime.now(UTC), 100) == 3
    assert profile(engine, RECENT_ORPHAN)[0] is not None


def test_a_returning_user_gets_their_profile_back_from_the_login_upsert(engine: Engine) -> None:
    populate(engine)
    SqlOrphanedUserStore(engine).scrub_batch(days_ago(30), 100)
    with engine.begin() as conn:
        conn.execute(t.users.update().where(t.users.c.id == OLD_ORPHAN).values(display_name="Back"))
    assert profile(engine, OLD_ORPHAN)[0] == "Back"


class TestPrivileges:
    def test_maintenance_can_execute_but_cannot_update_users(self, engine: Engine) -> None:
        populate(engine)
        with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE nave_maintenance"))
            conn.execute(text("UPDATE users SET email = NULL"))
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE nave_maintenance"))
            conn.execute(text("SELECT public.nave_scrub_orphan_users(:b, 10)"), {"b": days_ago(30)})

    def test_the_app_role_and_public_cannot_execute_it(self, engine: Engine) -> None:
        with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE nave_app"))
            conn.execute(text("SELECT public.nave_scrub_orphan_users(now(), 10)"))
        with engine.connect() as conn:
            allowed: bool = conn.execute(
                text(
                    "SELECT has_function_privilege('public', "
                    "'nave_scrub_orphan_users(timestamptz, integer)', 'EXECUTE')"
                )
            ).scalar_one()
        assert allowed is False

    def test_the_function_pins_its_search_path(self, engine: Engine) -> None:
        with engine.connect() as conn:
            config: list[str] = conn.execute(
                text("SELECT proconfig FROM pg_proc WHERE proname = 'nave_scrub_orphan_users'")
            ).scalar_one()
        assert any(str(c).startswith("search_path=") for c in config)
