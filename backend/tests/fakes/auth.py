"""Dobles de identidad y composición del login sobre el almacén en memoria."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from nave.application.dto.auth import OidcIdentity, SessionPolicy
from nave.application.dto.platform import ComponentHealth, HealthComponent, HealthStatus
from nave.application.errors import AuthenticationError, IdentityProviderUnavailableError
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.services.authorization import Authorizer
from nave.application.use_cases.browse import (
    GetFinding,
    GetScanVerdict,
    ListMembers,
    ListMyOrganizations,
    ListRepositories,
    ListScans,
)
from nave.application.use_cases.insights import (
    ExportFindings,
    GetOrganizationStats,
    GetPolicyHistory,
    GetScannerReport,
    ListAcceptedRisks,
    ListAuditEntries,
    ListDependencyFindings,
    ListFailedScans,
    ListOrganizationScans,
    ListPullRequests,
    ListRuleCatalog,
    ListTriageQueue,
)
from nave.application.use_cases.login import Authenticate, BeginLogin, CompleteLogin, Logout
from nave.application.use_cases.manage_policy import UpdatePolicy
from nave.application.use_cases.my_sessions import (
    ListMySessions,
    RevokeOtherSessions,
    RevokeSession,
)
from nave.application.use_cases.platform import (
    GetPlatformHealth,
    GetWebhookActivity,
    IsPlatformAdmin,
    ListPlatformOrganizations,
    ListPlatformUsers,
    RevokeUserSessions,
    SetMembershipAsPlatform,
)
from nave.delivery.http.api import RestDependencies
from nave.delivery.http.app import ApiDependencies, create_app
from nave.delivery.http.auth import AuthDependencies
from nave.delivery.http.rate_limit import RateLimiter
from nave.delivery.http.security import SessionGuard
from nave.delivery.http.webhook import WebhookDependencies
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.role import Role
from nave.infrastructure.export.finding_export import FindingExporter
from nave.infrastructure.vcs.webhook_verifier import WebhookVerifier
from tests.fakes.installations import FakeSyncQueue
from tests.fakes.memory import FakeClock, SequentialIds, Store
from tests.unit.application.builders import ORG_ID
from tests.unit.application.harness import Harness, make_harness

PUBLIC_ORIGIN = "https://nave.test"
POLICY = SessionPolicy(absolute=timedelta(hours=12), idle=timedelta(minutes=30))
ALICE = OidcIdentity(issuer="https://idp.test", subject="alice-sub", display_name="Alice")


@dataclass
class FakeProbes:
    """Lo que la API ve en vivo: base de datos y broker, siempre sanos salvo que se diga."""

    clock: FakeClock
    broker_down: bool = False

    def probe(self) -> Sequence[ComponentHealth]:
        now = self.clock.now()
        broker = HealthStatus.DOWN if self.broker_down else HealthStatus.OK
        return [
            ComponentHealth.of(HealthComponent.DATABASE, HealthStatus.OK, "Responde", now, 3),
            ComponentHealth.of(HealthComponent.BROKER, broker, "Responde", now, 2),
        ]


@dataclass
class FakeOidc:
    """IdP de pruebas: el código `good` identifica a Alice; otros códigos, según se configure."""

    identities: dict[str, OidcIdentity] = field(default_factory=lambda: {"good": ALICE})
    down: bool = False
    urls: list[dict[str, str]] = field(default_factory=list)
    exchanged: list[tuple[str, str, str]] = field(default_factory=list)

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        if self.down:
            raise IdentityProviderUnavailableError("down")
        self.urls.append({"state": state, "nonce": nonce, "challenge": code_challenge})
        return f"https://idp.test/authorize?state={state}"

    def complete(self, *, code: str, code_verifier: str, nonce: str) -> OidcIdentity:
        self.exchanged.append((code, code_verifier, nonce))
        if self.down:
            raise IdentityProviderUnavailableError("down")
        try:
            return self.identities[code]
        except KeyError:
            raise AuthenticationError from None


class CountingTokens:
    """Tokens deterministas de 43 caracteres, distintos en cada llamada."""

    def __init__(self) -> None:
        self.n = 0

    def new_token(self) -> str:
        self.n += 1
        return f"{self.n:043d}"


@dataclass
class AuthKit:
    begin: BeginLogin
    complete: CompleteLogin
    authenticate: Authenticate
    logout: Logout
    oidc: FakeOidc
    tokens: CountingTokens
    deps: AuthDependencies
    limiter: RateLimiter


def make_auth(
    store: Store,
    clock: FakeClock,
    *,
    oidc: FakeOidc | None = None,
    uow_factory: UnitOfWorkFactory | None = None,
) -> AuthKit:
    oidc = oidc or FakeOidc()
    factory = uow_factory or store.uow
    tokens = CountingTokens()
    ids = SequentialIds()
    begin = BeginLogin(uow_factory=factory, oidc=oidc, clock=clock, secrets=tokens)
    complete = CompleteLogin(
        uow_factory=factory, oidc=oidc, clock=clock, ids=ids, secrets=tokens, policy=POLICY
    )
    authenticate = Authenticate(uow_factory=factory, clock=clock, policy=POLICY)
    logout = Logout(uow_factory=factory, clock=clock)
    limiter = RateLimiter(clock=clock)
    deps = AuthDependencies(
        begin=begin,
        complete=complete,
        logout=logout,
        guard=SessionGuard(authenticate=authenticate, public_origin=PUBLIC_ORIGIN, limiter=limiter),
        limiter=limiter,
    )
    return AuthKit(begin, complete, authenticate, logout, oidc, tokens, deps, limiter)


def auth_deps_for_tests() -> AuthDependencies:
    return make_auth(Store(), FakeClock()).deps


@dataclass
class TestApp:
    __test__ = False  # no es una clase de test para pytest

    app: Flask
    kit: AuthKit
    store: Store
    clock: FakeClock
    harness: Harness

    def browser(self) -> FlaskClient:
        return self.app.test_client()

    def sign_in(self, client: FlaskClient, code: str = "good") -> TestResponse:
        """Recorre login → callback como lo haría el navegador."""
        client.get("/api/v1/auth/login", base_url=PUBLIC_ORIGIN)
        state = self.kit.oidc.urls[-1]["state"]
        return client.get(
            "/api/v1/auth/callback",
            query_string={"state": state, "code": code},
            base_url=PUBLIC_ORIGIN,
        )

    def alice(self) -> UserId:
        return next(u.id for u in self.store.state.users.values() if u.subject == ALICE.subject)

    def sign_in_as(self, role: Role | None, *, org: OrganizationId = ORG_ID) -> "Api":
        """Inicia sesión como Alice y le da `role` en `org` (sin rol: sin pertenencia)."""
        client = self.browser()
        self.sign_in(client)
        if role is not None:
            self.store.state.memberships[(self.alice(), org)] = role
        return Api(self, client, self.csrf_token(client))

    def csrf_token(self, client: FlaskClient) -> str:
        body = client.get("/api/v1/session", base_url=PUBLIC_ORIGIN).get_json()
        return str(body["csrf_token"])


def api_deps(
    webhook: WebhookDependencies,
    readiness: Sequence[Callable[[], None]] = (),
    harness: Harness | None = None,
    oidc: FakeOidc | None = None,
    trusted_proxy_hops: int = 0,
) -> tuple[ApiDependencies, AuthKit, Harness]:
    harness = harness or make_harness()
    kit = make_auth(harness.store, harness.clock, oidc=oidc)
    authorizer = Authorizer(uow_factory=harness.store.uow, clock=harness.clock)
    uow = harness.store.uow
    rest = RestDependencies(
        guard=kit.deps.guard,
        organizations=ListMyOrganizations(uow_factory=uow),
        repositories=ListRepositories(uow_factory=uow, authorizer=authorizer),
        members=ListMembers(uow_factory=uow, authorizer=authorizer),
        manage_membership=harness.members,
        list_scans=ListScans(uow_factory=uow, authorizer=authorizer),
        get_scan=harness.get_scan,
        scan_logs=harness.logs,
        verdict=GetScanVerdict(uow_factory=uow, authorizer=authorizer),
        start_scan=harness.start,
        cancel_scan=harness.cancel,
        list_findings=harness.list_findings,
        get_finding=GetFinding(uow_factory=uow, authorizer=authorizer),
        triage=harness.triage,
        org_stats=GetOrganizationStats(uow_factory=uow, authorizer=authorizer, clock=harness.clock),
        org_scans=ListOrganizationScans(uow_factory=uow, authorizer=authorizer),
        accepted_risks=ListAcceptedRisks(uow_factory=uow, authorizer=authorizer),
        rule_catalog=ListRuleCatalog(uow_factory=uow, authorizer=authorizer),
        dependencies=ListDependencyFindings(uow_factory=uow, authorizer=authorizer),
        policy_history=GetPolicyHistory(uow_factory=uow, authorizer=authorizer),
        audit_log=ListAuditEntries(uow_factory=uow, authorizer=authorizer),
        pull_requests=ListPullRequests(uow_factory=uow, authorizer=authorizer),
        triage_queue=ListTriageQueue(uow_factory=uow, authorizer=authorizer),
        failed_scans=ListFailedScans(uow_factory=uow, authorizer=authorizer),
        scanner_report=GetScannerReport(uow_factory=uow, authorizer=authorizer),
        export=ExportFindings(uow_factory=uow, authorizer=authorizer, exporter=FindingExporter()),
        my_sessions=ListMySessions(uow_factory=uow, clock=harness.clock, policy=POLICY),
        revoke_session=RevokeSession(uow_factory=uow, clock=harness.clock, policy=POLICY),
        revoke_other_sessions=RevokeOtherSessions(
            uow_factory=uow, clock=harness.clock, policy=POLICY
        ),
        update_policy=UpdatePolicy(
            uow_factory=uow, authorizer=authorizer, clock=harness.clock, ids=SequentialIds()
        ),
        me=IsPlatformAdmin(uow_factory=uow, clock=harness.clock),
        platform_users=ListPlatformUsers(uow_factory=uow, clock=harness.clock),
        platform_organizations=ListPlatformOrganizations(uow_factory=uow, clock=harness.clock),
        platform_membership=SetMembershipAsPlatform(uow_factory=uow, clock=harness.clock),
        platform_revoke_sessions=RevokeUserSessions(uow_factory=uow, clock=harness.clock),
        platform_health=GetPlatformHealth(
            uow_factory=uow, clock=harness.clock, probes=FakeProbes(harness.clock)
        ),
        platform_webhooks=GetWebhookActivity(uow_factory=uow, clock=harness.clock),
    )
    deps = ApiDependencies(
        webhook=webhook,
        auth=kit.deps,
        rest=rest,
        readiness=readiness,
        trusted_proxy_hops=trusted_proxy_hops,
    )
    return deps, kit, harness


def make_test_app(oidc: FakeOidc | None = None, *, trusted_proxy_hops: int = 0) -> TestApp:
    webhook_stub = WebhookDependencies(
        verifier=WebhookVerifier([b"k" * 40]),
        handle_event=None,  # type: ignore[arg-type]
        sync_queue=FakeSyncQueue(),
    )
    harness = make_harness()
    webhook = WebhookDependencies(
        verifier=webhook_stub.verifier, handle_event=harness.webhook, sync_queue=FakeSyncQueue()
    )
    deps, kit, harness = api_deps(
        webhook, harness=harness, oidc=oidc, trusted_proxy_hops=trusted_proxy_hops
    )
    return TestApp(create_app(deps), kit, harness.store, harness.clock, harness)


class Api:
    """Cliente ya autenticado: añade `Origin` y CSRF a todas las peticiones."""

    def __init__(self, app: TestApp, client: FlaskClient, csrf: str) -> None:
        self.app = app
        self.client = client
        self.csrf = csrf

    def call(  # noqa: PLR0913 - doble de test con muchas variantes
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        content_type: str | None = None,
    ) -> TestResponse:
        sent = {"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": self.csrf, **(headers or {})}
        return self.client.open(
            f"/api/v1{path}",
            method=method,
            json=json,
            data=data,
            content_type=content_type,
            headers=sent,
            base_url=PUBLIC_ORIGIN,
        )

    def get(self, path: str) -> TestResponse:
        return self.call("GET", path)
