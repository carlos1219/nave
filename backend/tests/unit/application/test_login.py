"""Casos de uso de login, autenticación y logout (ADR 0016)."""

from typing import Any
from uuid import UUID

import pytest

from nave.application.dto.auth import OidcIdentity
from nave.application.errors import (
    AuthenticationError,
    IdentityProviderUnavailableError,
    NotFoundError,
    TransientError,
)
from nave.application.services.session_tokens import hash_token, pkce_challenge
from nave.application.use_cases import login
from nave.application.use_cases.bootstrap_access import AmbiguousOrganizationError, BootstrapAccess
from nave.domain.entities.tenancy import Organization, OrganizationStatus
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role
from tests.fakes.auth import ALICE, POLICY, AuthKit, FakeOidc, make_auth
from tests.fakes.memory import FakeClock, SequentialIds, Store
from tests.unit.application.builders import ORG_ID, seed_tenancy


class World:
    def __init__(self, oidc: FakeOidc | None = None) -> None:
        self.store = Store()
        seed_tenancy(self.store)
        self.clock = FakeClock()
        self.kit: AuthKit = make_auth(self.store, self.clock, oidc=oidc)

    def login(self, code: str = "good") -> str:
        """Hace un login completo y devuelve el valor de la cookie de sesión."""
        started = self.kit.begin.execute()
        state = self.kit.oidc.urls[-1]["state"]
        return self.kit.complete.execute(state=state, code=code, binding=started.binding).token


@pytest.fixture
def world() -> World:
    return World()


class TestBeginLogin:
    def test_state_nonce_and_verifier_are_distinct_and_stored_only_as_needed(
        self, world: World
    ) -> None:
        started = world.kit.begin.execute()
        sent = world.kit.oidc.urls[0]
        flows = world.store.state.login_flows
        assert len(flows) == 1
        flow = next(iter(flows.values()))
        assert flow.state_hash == hash_token(sent["state"])
        assert flow.binding_hash == hash_token(started.binding)
        assert pkce_challenge(flow.code_verifier) == sent["challenge"]
        assert len({sent["state"], flow.nonce, flow.code_verifier, started.binding}) == 4
        assert flow.expires_at - flow.created_at == login.FLOW_TTL
        # Ni el state ni el binding en claro se guardan.
        assert sent["state"].encode() not in flow.state_hash

    def test_expired_flows_and_sessions_are_cleaned_up(self, world: World) -> None:
        world.login()
        world.kit.begin.execute()  # un flujo vivo
        world.clock.advance(hours=13)
        world.kit.begin.execute()
        assert len(world.store.state.login_flows) == 1
        assert world.store.state.sessions == {}

    def test_too_many_flows_in_progress_is_a_transient_error(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(login, "MAX_LIVE_FLOWS", 2)
        world.kit.begin.execute()
        world.kit.begin.execute()
        with pytest.raises(TransientError):
            world.kit.begin.execute()
        assert len(world.store.state.login_flows) == 2

    def test_idp_down_stores_nothing(self) -> None:
        w = World(FakeOidc(down=True))
        with pytest.raises(IdentityProviderUnavailableError):
            w.kit.begin.execute()
        assert w.store.state.login_flows == {}


class TestCompleteLogin:
    def test_creates_the_user_and_a_hashed_session(self, world: World) -> None:
        token = world.login()
        state = world.store.state
        (user,) = state.users.values()
        assert (user.issuer, user.subject, user.display_name) == (
            ALICE.issuer,
            ALICE.subject,
            "Alice",
        )
        record = state.sessions[hash_token(token)]
        assert record.user_id == user.id
        assert record.expires_at - record.created_at == POLICY.absolute
        assert token.encode() not in record.token_hash
        assert record.csrf_token != token
        assert [a.action for a in state.audit] == ["session.created"]
        assert state.login_flows == {}

    def test_a_new_user_has_no_access_to_any_organization(self, world: World) -> None:
        world.login()
        (user,) = world.store.state.users.values()
        assert not [k for k in world.store.state.memberships if k[0] == user.id]

    def test_the_idp_receives_the_stored_verifier_and_nonce(self, world: World) -> None:
        world.login()
        code, verifier, nonce = world.kit.oidc.exchanged[0]
        sent = world.kit.oidc.urls[0]
        assert code == "good"
        assert pkce_challenge(verifier) == sent["challenge"]
        assert nonce == sent["nonce"]

    def test_the_same_identity_keeps_one_user_and_a_new_session_each_time(
        self, world: World
    ) -> None:
        first, second = world.login(), world.login()
        assert first != second
        assert len(world.store.state.users) == 1
        assert len(world.store.state.sessions) == 2

    def test_another_subject_is_another_user(self, world: World) -> None:
        bob = OidcIdentity(issuer=ALICE.issuer, subject="bob-sub", display_name="Bob")
        world.kit.oidc.identities["bob"] = bob
        world.login()
        world.login("bob")
        assert len(world.store.state.users) == 2

    def test_the_state_is_single_use(self, world: World) -> None:
        started = world.kit.begin.execute()
        state = world.kit.oidc.urls[0]["state"]
        world.kit.complete.execute(state=state, code="good", binding=started.binding)
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state=state, code="good", binding=started.binding)
        assert len(world.store.state.sessions) == 1

    def test_a_stolen_state_with_another_browsers_binding_fails_and_burns_the_flow(
        self, world: World
    ) -> None:
        started = world.kit.begin.execute()
        state = world.kit.oidc.urls[0]["state"]
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state=state, code="good", binding="x" * 43)
        # La víctima tampoco puede ya usar ese state: hay que empezar de nuevo.
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state=state, code="good", binding=started.binding)
        assert world.store.state.sessions == {}
        assert world.kit.oidc.exchanged == []

    def test_a_missing_binding_cookie_fails(self, world: World) -> None:
        world.kit.begin.execute()
        state = world.kit.oidc.urls[0]["state"]
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state=state, code="good", binding=None)

    def test_an_expired_flow_fails(self, world: World) -> None:
        started = world.kit.begin.execute()
        state = world.kit.oidc.urls[0]["state"]
        world.clock.advance(seconds=login.FLOW_TTL.total_seconds() + 1)
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state=state, code="good", binding=started.binding)

    def test_a_rejected_code_burns_the_flow_and_creates_nothing(self, world: World) -> None:
        started = world.kit.begin.execute()
        state = world.kit.oidc.urls[0]["state"]
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state=state, code="bad", binding=started.binding)
        assert world.store.state.login_flows == {}
        assert world.store.state.users == {}
        assert world.store.state.sessions == {}

    def test_an_unavailable_idp_is_transient_and_creates_nothing(self, world: World) -> None:
        started = world.kit.begin.execute()
        state = world.kit.oidc.urls[0]["state"]
        world.kit.oidc.down = True
        with pytest.raises(IdentityProviderUnavailableError):
            world.kit.complete.execute(state=state, code="good", binding=started.binding)
        assert world.store.state.sessions == {}

    @pytest.mark.parametrize("bad", [None, 7, "", "short", "!" * 43, "a" * 200, b"x" * 43])
    def test_malformed_state_or_binding_never_reaches_the_database(
        self, world: World, bad: Any
    ) -> None:
        good = "a" * 43
        commits = world.store.commits
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state=bad, code="good", binding=good)
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state=good, code="good", binding=bad)
        assert world.store.commits == commits

    @pytest.mark.parametrize("bad", [None, 7, "", "a" * 3000, "a\nb"])
    def test_malformed_code(self, world: World, bad: Any) -> None:
        with pytest.raises(AuthenticationError):
            world.kit.complete.execute(state="a" * 43, code=bad, binding="b" * 43)


class TestAuthenticate:
    def test_a_valid_session_identifies_the_user(self, world: World) -> None:
        token = world.login()
        principal = world.kit.authenticate.execute(token)
        (user,) = world.store.state.users.values()
        assert principal.user_id == user.id
        assert principal.display_name == "Alice"
        assert principal.csrf_token == world.store.state.sessions[hash_token(token)].csrf_token

    @pytest.mark.parametrize("bad", [None, "", "x", 5, "a" * 43, "!" * 43])
    def test_unknown_or_malformed_tokens(self, world: World, bad: Any) -> None:
        with pytest.raises(AuthenticationError):
            world.kit.authenticate.execute(bad)

    def test_the_absolute_lifetime_ends_the_session_even_if_active(self, world: World) -> None:
        token = world.login()
        for _ in range(26):
            world.clock.advance(minutes=29)
            try:
                world.kit.authenticate.execute(token)
            except AuthenticationError:
                break
        else:
            pytest.fail("the session outlived its absolute lifetime")
        assert world.clock.current - world.store.state.audit[0].occurred_at >= POLICY.absolute
        assert world.store.state.sessions == {}

    def test_idle_sessions_expire_and_are_deleted(self, world: World) -> None:
        token = world.login()
        world.clock.advance(minutes=31)
        with pytest.raises(AuthenticationError):
            world.kit.authenticate.execute(token)
        assert world.store.state.sessions == {}

    def test_activity_extends_the_idle_window(self, world: World) -> None:
        token = world.login()
        for _ in range(5):
            world.clock.advance(minutes=20)
            world.kit.authenticate.execute(token)

    def test_last_seen_is_written_at_most_once_per_interval(self, world: World) -> None:
        token = world.login()
        commits = world.store.commits
        world.clock.advance(seconds=30)
        world.kit.authenticate.execute(token)
        assert world.store.commits == commits
        world.clock.advance(seconds=31)
        world.kit.authenticate.execute(token)
        assert world.store.commits == commits + 1

    def test_a_session_of_a_deleted_token_cannot_be_revived(self, world: World) -> None:
        token = world.login()
        world.kit.logout.execute(world.kit.authenticate.execute(token), token)
        with pytest.raises(AuthenticationError):
            world.kit.authenticate.execute(token)


class TestLogout:
    def test_deletes_the_session_and_audits_once(self, world: World) -> None:
        token = world.login()
        principal = world.kit.authenticate.execute(token)
        world.kit.logout.execute(principal, token)
        world.kit.logout.execute(principal, token)
        assert world.store.state.sessions == {}
        actions = [a.action for a in world.store.state.audit]
        assert actions.count("session.ended") == 1

    def test_other_sessions_of_the_user_survive(self, world: World) -> None:
        first, second = world.login(), world.login()
        world.kit.logout.execute(world.kit.authenticate.execute(first), first)
        world.kit.authenticate.execute(second)


class TestBootstrapAccess:
    def make(self, world: World) -> BootstrapAccess:
        return BootstrapAccess(uow_factory=world.store.uow, clock=world.clock)

    def alice_id(self, world: World) -> UserId:
        world.login()
        (user,) = world.store.state.users.values()
        return user.id

    def test_grants_admin_without_an_acting_user_and_audits_it(self, world: World) -> None:
        user = self.alice_id(world)
        self.make(world).grant_admin(organization_login="acme", user=user)
        assert world.store.state.memberships[(user, ORG_ID)] is Role.ADMIN
        entry = world.store.state.audit[-1]
        assert (entry.action, entry.actor, entry.organization_id) == (
            "membership.bootstrapped",
            None,
            ORG_ID,
        )

    def test_unknown_user_or_organization(self, world: World) -> None:
        service = self.make(world)
        with pytest.raises(NotFoundError):
            service.grant_admin(organization_login="acme", user=UserId(SequentialIds().new_uuid()))
        user = self.alice_id(world)
        with pytest.raises(NotFoundError):
            service.grant_admin(organization_login="nope", user=user)

    def test_uninstalled_organizations_are_ignored_and_duplicates_refused(
        self, world: World
    ) -> None:
        user = self.alice_id(world)
        for n, status in ((2, OrganizationStatus.UNINSTALLED), (3, OrganizationStatus.ACTIVE)):
            self.add_org(world, n, "dup", status)
        self.make(world).grant_admin(organization_login="dup", user=user)
        self.add_org(world, 4, "dup", OrganizationStatus.ACTIVE)
        with pytest.raises(AmbiguousOrganizationError):
            self.make(world).grant_admin(organization_login="dup", user=user)

    @staticmethod
    def add_org(world: World, n: int, login: str, status: OrganizationStatus) -> None:
        org_id = OrganizationId(UUID(int=n))
        world.store.state.organizations[org_id] = Organization(
            id=org_id, github_installation_id=900 + n, account_login=login, status=status
        )

    def test_list_users_most_recent_first(self, world: World) -> None:
        world.login()
        world.clock.advance(hours=1)
        world.kit.oidc.identities["bob"] = OidcIdentity(issuer=ALICE.issuer, subject="bob")
        world.login("bob")
        users = self.make(world).list_users(limit=10)
        assert [u.subject for u in users] == ["bob", "alice-sub"]
        assert self.make(world).list_users(limit=1)[0].subject == "bob"

    def test_a_membership_set_without_grantor_is_admin_but_loses_nothing(
        self, world: World
    ) -> None:
        # El último admin sigue protegido por ManageMembership: el CLI solo añade.
        user = self.alice_id(world)
        before = dict(world.store.state.memberships)
        self.make(world).grant_admin(organization_login="acme", user=user)
        assert set(before) < set(world.store.state.memberships)
