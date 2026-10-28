"""Punto de entrada del servicio `api` (ADR 0008, ADR 0015).

`gunicorn "nave.bootstrap.api:api_app()"`. Aquí, y solo aquí, se juntan la configuración
de la API y las implementaciones concretas. La API no recibe credenciales del worker."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from celery import Celery
from flask import Flask
from sqlalchemy import Engine, text

from nave.application.services.authorization import Authorizer
from nave.application.services.scan_scheduler import ScanScheduler
from nave.application.use_cases.browse import (
    GetFinding,
    GetScanVerdict,
    ListMembers,
    ListMyOrganizations,
    ListRepositories,
    ListScans,
)
from nave.application.use_cases.cancel_scan import CancelScan
from nave.application.use_cases.handle_github_event import HandleGithubEvent
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
from nave.application.use_cases.manage_membership import ManageMembership
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
    RecordWebhookEvent,
    RevokeUserSessions,
    SetMembershipAsPlatform,
)
from nave.application.use_cases.queries import GetScan, ListFindings, ListScanLogs
from nave.application.use_cases.start_scan import StartScan
from nave.application.use_cases.triage_finding import TriageFinding
from nave.bootstrap.api_config import ApiSettings
from nave.delivery.http.api import RestDependencies
from nave.delivery.http.app import ApiDependencies, create_app
from nave.delivery.http.auth import AuthDependencies
from nave.delivery.http.rate_limit import RateLimiter
from nave.delivery.http.security import SessionGuard
from nave.delivery.http.webhook import WebhookDependencies
from nave.infrastructure.auth.oidc import HttpOidcTransport, OidcClient
from nave.infrastructure.export.finding_export import FindingExporter
from nave.infrastructure.observability.health_probes import HealthProbes, ProbeTargets
from nave.infrastructure.observability.logging import configure_logging
from nave.infrastructure.observability.webhook_stats import BufferedWebhookStats
from nave.infrastructure.persistence.engine import create_database_engine
from nave.infrastructure.persistence.platform import SqlWebhookCounterStore
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from nave.infrastructure.queue.celery_app import create_celery_app
from nave.infrastructure.queue.celery_queue_adapter import (
    CeleryInstallationSyncQueue,
    CeleryScanQueue,
)
from nave.infrastructure.system import RandomIdGenerator, RandomTokenSource, SystemClock
from nave.infrastructure.vcs.webhook_verifier import WebhookVerifier


@dataclass(frozen=True, slots=True)
class ApiContainer:
    settings: ApiSettings
    engine: Engine
    celery: Celery
    app: Flask

    def close(self) -> None:
        self.engine.dispose()


def build_api(settings: ApiSettings) -> ApiContainer:
    engine = create_database_engine(settings.database_url)
    celery = create_celery_app(settings.celery)
    uow_factory = SqlUnitOfWorkFactory(engine)
    clock = SystemClock()
    queue = CeleryScanQueue(celery, max_queue_depth=settings.celery.max_queue_depth)
    handle_event = HandleGithubEvent(
        uow_factory=uow_factory,
        queue=queue,
        scheduler=ScanScheduler(clock=clock, ids=RandomIdGenerator()),
        clock=clock,
        scan_fork_pull_requests=settings.scan_fork_pull_requests,
    )

    oidc = OidcClient(
        settings.oidc,
        HttpOidcTransport(
            origin_url=settings.oidc.issuer,
            allow_insecure_loopback=settings.oidc.allow_insecure_loopback,
            proxy_url=settings.oidc.proxy_url,
        ),
        clock,
    )
    secrets = RandomTokenSource()
    authenticate = Authenticate(uow_factory=uow_factory, clock=clock, policy=settings.session)

    limiter = RateLimiter(clock=clock)
    guard = SessionGuard(
        authenticate=authenticate, public_origin=settings.public_origin, limiter=limiter
    )
    authorizer = Authorizer(uow_factory=uow_factory, clock=clock)
    scheduler = ScanScheduler(clock=clock, ids=RandomIdGenerator())
    rest = RestDependencies(
        guard=guard,
        organizations=ListMyOrganizations(uow_factory=uow_factory),
        repositories=ListRepositories(uow_factory=uow_factory, authorizer=authorizer),
        members=ListMembers(uow_factory=uow_factory, authorizer=authorizer),
        manage_membership=ManageMembership(
            uow_factory=uow_factory, authorizer=authorizer, clock=clock
        ),
        list_scans=ListScans(uow_factory=uow_factory, authorizer=authorizer),
        get_scan=GetScan(uow_factory=uow_factory, authorizer=authorizer),
        scan_logs=ListScanLogs(uow_factory=uow_factory, authorizer=authorizer),
        verdict=GetScanVerdict(uow_factory=uow_factory, authorizer=authorizer),
        start_scan=StartScan(
            uow_factory=uow_factory,
            queue=queue,
            scheduler=scheduler,
            authorizer=authorizer,
            clock=clock,
        ),
        cancel_scan=CancelScan(
            uow_factory=uow_factory, queue=queue, authorizer=authorizer, clock=clock
        ),
        list_findings=ListFindings(uow_factory=uow_factory, authorizer=authorizer),
        get_finding=GetFinding(uow_factory=uow_factory, authorizer=authorizer),
        triage=TriageFinding(
            uow_factory=uow_factory, authorizer=authorizer, clock=clock, ids=RandomIdGenerator()
        ),
        org_stats=GetOrganizationStats(uow_factory=uow_factory, authorizer=authorizer, clock=clock),
        org_scans=ListOrganizationScans(uow_factory=uow_factory, authorizer=authorizer),
        accepted_risks=ListAcceptedRisks(uow_factory=uow_factory, authorizer=authorizer),
        rule_catalog=ListRuleCatalog(uow_factory=uow_factory, authorizer=authorizer),
        dependencies=ListDependencyFindings(uow_factory=uow_factory, authorizer=authorizer),
        policy_history=GetPolicyHistory(uow_factory=uow_factory, authorizer=authorizer),
        audit_log=ListAuditEntries(uow_factory=uow_factory, authorizer=authorizer),
        pull_requests=ListPullRequests(uow_factory=uow_factory, authorizer=authorizer),
        triage_queue=ListTriageQueue(uow_factory=uow_factory, authorizer=authorizer),
        failed_scans=ListFailedScans(uow_factory=uow_factory, authorizer=authorizer),
        scanner_report=GetScannerReport(uow_factory=uow_factory, authorizer=authorizer),
        export=ExportFindings(
            uow_factory=uow_factory, authorizer=authorizer, exporter=FindingExporter()
        ),
        my_sessions=ListMySessions(uow_factory=uow_factory, clock=clock, policy=settings.session),
        revoke_session=RevokeSession(uow_factory=uow_factory, clock=clock, policy=settings.session),
        revoke_other_sessions=RevokeOtherSessions(
            uow_factory=uow_factory, clock=clock, policy=settings.session
        ),
        update_policy=UpdatePolicy(
            uow_factory=uow_factory, authorizer=authorizer, clock=clock, ids=RandomIdGenerator()
        ),
        me=IsPlatformAdmin(uow_factory=uow_factory, clock=clock),
        platform_users=ListPlatformUsers(uow_factory=uow_factory, clock=clock),
        platform_organizations=ListPlatformOrganizations(uow_factory=uow_factory, clock=clock),
        platform_membership=SetMembershipAsPlatform(uow_factory=uow_factory, clock=clock),
        platform_revoke_sessions=RevokeUserSessions(uow_factory=uow_factory, clock=clock),
        # La API solo ve base de datos, broker y colas; el resto llega del latido del worker.
        platform_health=GetPlatformHealth(
            uow_factory=uow_factory,
            clock=clock,
            probes=HealthProbes(
                engine=engine,
                celery=celery,
                clock=clock,
                targets=ProbeTargets(max_queue_depth=settings.celery.max_queue_depth),
            ),
        ),
        platform_webhooks=GetWebhookActivity(uow_factory=uow_factory, clock=clock),
    )

    def check_database() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def check_broker() -> None:
        with celery.connection_for_write() as connection:
            connection.default_channel.client.ping()

    app = create_app(
        ApiDependencies(
            webhook=WebhookDependencies(
                verifier=WebhookVerifier(settings.webhook_secrets),
                handle_event=handle_event,
                sync_queue=CeleryInstallationSyncQueue(
                    celery, max_queue_depth=settings.celery.max_queue_depth
                ),
                record_event=RecordWebhookEvent(
                    stats=BufferedWebhookStats(store=SqlWebhookCounterStore(engine), clock=clock)
                ),
            ),
            auth=AuthDependencies(
                begin=BeginLogin(uow_factory=uow_factory, oidc=oidc, clock=clock, secrets=secrets),
                complete=CompleteLogin(
                    uow_factory=uow_factory,
                    oidc=oidc,
                    clock=clock,
                    ids=RandomIdGenerator(),
                    secrets=secrets,
                    policy=settings.session,
                ),
                logout=Logout(uow_factory=uow_factory, clock=clock),
                guard=guard,
                limiter=limiter,
            ),
            rest=rest,
            readiness=(check_database, check_broker),
            trusted_proxy_hops=settings.trusted_proxy_hops,
        )
    )
    return ApiContainer(settings=settings, engine=engine, celery=celery, app=app)


def build_api_from_environ(environ: Mapping[str, str]) -> ApiContainer:
    return build_api(ApiSettings.from_environ(environ))


def api_app() -> Flask:
    """Fábrica para `gunicorn "nave.bootstrap.api:api_app()"`."""
    settings = ApiSettings.from_environ(os.environ)
    configure_logging("nave-api", settings.log_level)
    return build_api(settings).app
