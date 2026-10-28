"""Composition root (CLAUDE.md §5.3): el único módulo que conoce las implementaciones
concretas. Las API, los workers y el scheduler construyen su `Container` aquí; ni los
casos de uso ni las tareas instancian dependencias por su cuenta."""

from dataclasses import dataclass
from datetime import timedelta

from celery import Celery
from sqlalchemy import Engine

from nave.application.ports.installations import InstallationDirectoryPort
from nave.application.ports.scanning import RepositoryFetcherPort, ScannerRegistryPort
from nave.application.ports.vcs import VcsReporterPort
from nave.application.use_cases.abandon_scan import AbandonScan
from nave.application.use_cases.execute_scan import ExecuteScan
from nave.application.use_cases.platform import RecordHealth
from nave.application.use_cases.process_scan_results import ProcessScanResults
from nave.application.use_cases.purge_expired_data import PurgeExpiredData
from nave.application.use_cases.purge_uninstalled_organizations import (
    PurgeUninstalledOrganizations,
)
from nave.application.use_cases.reconcile_installations import ReconcileInstallations
from nave.application.use_cases.relay_outbox import RelayOutbox
from nave.application.use_cases.report_to_vcs import ReportToVcs
from nave.application.use_cases.scrub_orphaned_users import ScrubOrphanedUsers
from nave.application.use_cases.sweep_stuck_scans import SweepStuckScans
from nave.application.use_cases.sync_installation import SyncInstallation
from nave.bootstrap.config import ConfigurationError, Settings
from nave.delivery.tasks.installation_tasks import (
    InstallationTaskDependencies,
    register_installation_tasks,
)
from nave.delivery.tasks.maintenance_tasks import (
    MaintenanceTaskDependencies,
    register_maintenance_tasks,
)
from nave.delivery.tasks.scan_tasks import ScanTaskDependencies, register_scan_tasks
from nave.infrastructure.observability.event_log import LogEventSink
from nave.infrastructure.observability.health_probes import HealthProbes, ProbeTargets
from nave.infrastructure.persistence.engine import create_database_engine
from nave.infrastructure.persistence.maintenance import (
    SqlOrganizationPurgeStore,
    SqlOrphanedUserStore,
    SqlOutboxStore,
    SqlRetentionStore,
    SqlScanSweepStore,
)
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from nave.infrastructure.policy.evaluator import QualityGateEvaluator
from nave.infrastructure.queue.celery_app import create_celery_app
from nave.infrastructure.queue.celery_queue_adapter import (
    CeleryInstallationSyncQueue,
    CeleryScanQueue,
)
from nave.infrastructure.system import RandomIdGenerator, SystemClock


@dataclass(frozen=True, slots=True)
class ExternalAdapters:
    """Adaptadores que hablan con sistemas externos y no dependen de la configuración
    de colas ni de base de datos: se inyectan para poder sustituirlos en tests."""

    fetcher: RepositoryFetcherPort
    registry: ScannerRegistryPort
    vcs: VcsReporterPort
    directory: InstallationDirectoryPort


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    engine: Engine
    maintenance_engine: Engine
    celery: Celery
    execute_scan: ExecuteScan
    report_to_vcs: ReportToVcs
    abandon_scan: AbandonScan
    relay_outbox: RelayOutbox
    sweep_scans: SweepStuckScans
    purge_expired_data: PurgeExpiredData
    purge_organizations: PurgeUninstalledOrganizations
    scrub_users: ScrubOrphanedUsers
    sync_installation: SyncInstallation
    reconcile_installations: ReconcileInstallations

    def close(self) -> None:
        self.engine.dispose()
        self.maintenance_engine.dispose()


def build_container(settings: Settings, adapters: ExternalAdapters) -> Container:
    """Construye el grafo de objetos del worker y registra sus tareas en Celery."""
    _check_time_budget(settings, adapters.registry)
    if settings.maintenance_database_url is None:
        raise ConfigurationError("NAVE_MAINTENANCE_DATABASE_URL is required for the worker")
    engine = create_database_engine(settings.database_url)
    # Credencial aparte (nave_maintenance): único rol con DELETE sobre las tablas con retención.
    maintenance_engine = create_database_engine(
        settings.maintenance_database_url, statement_timeout=timedelta(minutes=2), pool_size=2
    )
    celery = create_celery_app(settings.celery)
    uow_factory = SqlUnitOfWorkFactory(engine)
    clock = SystemClock()
    queue = CeleryScanQueue(celery, max_queue_depth=settings.celery.max_queue_depth)
    processor = ProcessScanResults(
        uow_factory=uow_factory,
        queue=queue,
        evaluator=QualityGateEvaluator(),
        clock=clock,
        ids=RandomIdGenerator(),
        max_findings_per_scanner=settings.max_findings_per_scanner,
        fingerprint_key=settings.fingerprint_key,
    )
    abandon = AbandonScan(uow_factory=uow_factory, queue=queue, clock=clock)
    container = Container(
        settings=settings,
        engine=engine,
        maintenance_engine=maintenance_engine,
        celery=celery,
        execute_scan=ExecuteScan(
            uow_factory=uow_factory,
            fetcher=adapters.fetcher,
            registry=adapters.registry,
            processor=processor,
            queue=queue,
            clock=clock,
            fetch_limits=settings.fetch_limits,
            scan_limits=settings.scan_limits,
        ),
        report_to_vcs=ReportToVcs(
            uow_factory=uow_factory,
            vcs=adapters.vcs,
            max_sarif_findings=settings.max_sarif_findings,
        ),
        abandon_scan=abandon,
        relay_outbox=RelayOutbox(store=SqlOutboxStore(engine, clock), sinks=(LogEventSink(),)),
        sweep_scans=SweepStuckScans(
            store=SqlScanSweepStore(engine),
            queue=queue,
            abandon=abandon,
            clock=clock,
            thresholds=settings.sweep,
        ),
        purge_expired_data=PurgeExpiredData(
            store=SqlRetentionStore(maintenance_engine), clock=clock, policy=settings.retention
        ),
        purge_organizations=PurgeUninstalledOrganizations(
            uow_factory=uow_factory,
            store=SqlOrganizationPurgeStore(maintenance_engine),
            clock=clock,
        ),
        scrub_users=ScrubOrphanedUsers(store=SqlOrphanedUserStore(maintenance_engine), clock=clock),
        sync_installation=SyncInstallation(
            uow_factory=uow_factory,
            directory=adapters.directory,
            clock=clock,
            ids=RandomIdGenerator(),
            allowed_accounts=settings.github.allowed_accounts if settings.github else (),
        ),
        reconcile_installations=ReconcileInstallations(
            uow_factory=uow_factory,
            directory=adapters.directory,
            queue=CeleryInstallationSyncQueue(
                celery, max_queue_depth=settings.celery.max_queue_depth
            ),
        ),
    )
    register_scan_tasks(
        celery,
        ScanTaskDependencies(
            execute_scan=container.execute_scan,
            report_to_vcs=container.report_to_vcs,
            abandon_scan=container.abandon_scan,
            retry=settings.celery.retry,
        ),
        scan_limits=settings.celery.scan,
        report_limits=settings.celery.report,
    )
    register_maintenance_tasks(
        celery,
        MaintenanceTaskDependencies(
            relay_outbox=container.relay_outbox,
            sweep_scans=container.sweep_scans,
            purge_expired_data=container.purge_expired_data,
            purge_organizations=container.purge_organizations,
            scrub_users=container.scrub_users,
            record_health=RecordHealth(
                uow_factory=uow_factory,
                clock=clock,
                probes=HealthProbes(
                    engine=engine,
                    celery=celery,
                    clock=clock,
                    targets=ProbeTargets(
                        runner_url=settings.runner.url if settings.runner else None,
                        egress_proxy_url=settings.github.egress_proxy_url
                        if settings.github
                        else None,
                        max_queue_depth=settings.celery.max_queue_depth,
                    ),
                ),
            ),
        ),
        limits=settings.celery.maintenance,
    )
    register_installation_tasks(
        celery,
        InstallationTaskDependencies(
            sync_installation=container.sync_installation,
            reconcile_installations=container.reconcile_installations,
            retry=settings.celery.retry,
        ),
        limits=settings.celery.maintenance,
    )
    return container


def _check_time_budget(settings: Settings, registry: ScannerRegistryPort) -> None:
    """El límite blando de la tarea debe cubrir fetch + todos los escáneres en serie; si
    no, una tarea sana moriría por tiempo a mitad de un escaneo legítimo."""
    scanners = len(tuple(registry.scanners()))
    needed = settings.fetch_limits.max_duration + scanners * settings.scan_limits.max_duration
    if settings.celery.scan.soft <= needed:
        raise ConfigurationError(
            "NAVE_SCAN_TASK_SOFT_SECONDS must exceed fetch time plus scan time of every scanner"
        )
