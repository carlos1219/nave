from dataclasses import dataclass
from enum import Enum

from nave.application.dto.scanning import FetchLimits, ScanLimits, ScannerOutput
from nave.application.dto.views import LogLevel
from nave.application.errors import (
    PermanentScanError,
    ScannerOutputInvalidError,
    ScanTimeoutError,
    TransientError,
)
from nave.application.ports.messaging import ScanQueuePort, ScanStep
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.scanning import RepositoryFetcherPort, ScannerRegistryPort
from nave.application.ports.system import Clock
from nave.application.use_cases.process_scan_results import (
    ProcessScanResults,
    ProcessScanResultsCommand,
)
from nave.domain.entities.scan import Scan
from nave.domain.entities.tenancy import can_be_scanned
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import ReasonCode, StatusReason


@dataclass(frozen=True, slots=True)
class ExecuteScanCommand:
    scan_id: ScanId


class ExecuteScanOutcome(Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    SKIPPED = "SKIPPED"


class _ScanStoppedError(Exception):
    """El escaneo pasó a un estado terminal (cancelado, sustituido) durante la ejecución."""


class ExecuteScan:
    """Obtiene el código, ejecuta los escáneres y delega el procesamiento (ADR 0009).

    - El workspace vive solo dentro del `with` del fetcher: se libera antes de procesar
      y también ante cualquier error.
    - Idempotente ante reentregas: un escaneo terminal se omite y uno a medias se
      retoma desde el fetch (el workspace anterior ya no existe).
    - Errores transitorios: se propagan para que la tarea reintente con backoff; el
      estado no cambia. Errores permanentes: FAILED con motivo saneado.
    - Entre pasos se relee el estado: si alguien canceló el escaneo, se detiene.
    """

    def __init__(  # noqa: PLR0913 - dependencias del caso de uso
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        fetcher: RepositoryFetcherPort,
        registry: ScannerRegistryPort,
        processor: ProcessScanResults,
        queue: ScanQueuePort,
        clock: Clock,
        fetch_limits: FetchLimits,
        scan_limits: ScanLimits,
    ) -> None:
        self._uow_factory = uow_factory
        self._fetcher = fetcher
        self._registry = registry
        self._processor = processor
        self._queue = queue
        self._clock = clock
        self._fetch_limits = fetch_limits
        self._scan_limits = scan_limits

    def execute(self, command: ExecuteScanCommand) -> ExecuteScanOutcome:
        try:
            scan, installation_id, repo = self._begin(command.scan_id)
            outputs = self._fetch_and_scan(scan, installation_id, repo)
        except _ScanStoppedError:
            return ExecuteScanOutcome.SKIPPED
        except TransientError as error:
            self._log(
                command.scan_id, LogLevel.WARNING, f"Transient error, will retry: {error.code}"
            )
            raise
        except ScanTimeoutError:
            return self._end(command.scan_id, StatusReason(ReasonCode.TIMEOUT), timed_out=True)
        except PermanentScanError as error:
            reason = StatusReason.from_untrusted(error.reason_code, error.detail)
            return self._end(command.scan_id, reason, timed_out=False)
        completed = self._processor.execute(ProcessScanResultsCommand(command.scan_id, outputs))
        if completed.status is ScanStatus.COMPLETED:
            return ExecuteScanOutcome.COMPLETED
        return ExecuteScanOutcome.SKIPPED

    def _begin(self, scan_id: ScanId) -> tuple[Scan, int, RepoRef]:
        with self._uow_factory() as uow:
            scan = uow.scans.get(scan_id)
            if scan.is_terminal:
                raise _ScanStoppedError
            repository = uow.tenants.get_repository(scan.repository_id)
            organization = uow.tenants.get_organization(repository.organization_id)
            allowed = can_be_scanned(repository, organization)
            if scan.status is ScanStatus.QUEUED:
                scan, event = scan.start_fetching(self._clock.now())
                scan = uow.scans.save(scan)
                uow.events.publish([event])
            if allowed:
                uow.scan_logs.append(
                    scan.id, LogLevel.INFO, "Fetching repository", self._clock.now()
                )
            uow.commit()
        if not allowed:
            # Se deshabilitó después de encolarlo: falla con su motivo, ya en FETCHING.
            raise PermanentScanError("repository is no longer enabled for scanning")
        return scan, organization.github_installation_id, repository.ref

    def _fetch_and_scan(
        self, scan: Scan, installation_id: int, repo: RepoRef
    ) -> tuple[ScannerOutput, ...]:
        scanners = tuple(self._registry.scanners())
        if not scanners:
            # Sin escáneres, el escaneo terminaría sin hallazgos y daría PASS: un "todo
            # limpio" falso por un error de configuración. Se falla de forma explícita.
            raise PermanentScanError("no scanners are enabled")
        outputs: list[ScannerOutput] = []
        with self._fetcher.checkout(
            installation_id=installation_id,
            repo=repo,
            commit=scan.commit,
            limits=self._fetch_limits,
        ) as workspace:
            self._advance_to_scanning(scan.id)
            for scanner in scanners:
                output = scanner.scan(workspace, self._scan_limits)
                if output.kind != scanner.kind:
                    raise ScannerOutputInvalidError("adapter returned output of another scanner")
                outputs.append(output)
        return tuple(outputs)

    def _advance_to_scanning(self, scan_id: ScanId) -> None:
        with self._uow_factory() as uow:
            scan = uow.scans.get(scan_id)
            if scan.is_terminal:
                raise _ScanStoppedError
            if scan.status is ScanStatus.FETCHING:
                scan, event = scan.start_scanning(self._clock.now())
                uow.scans.save(scan)
                uow.events.publish([event])
            uow.scan_logs.append(scan_id, LogLevel.INFO, "Running scanners", self._clock.now())
            uow.commit()

    def _end(self, scan_id: ScanId, reason: StatusReason, *, timed_out: bool) -> ExecuteScanOutcome:
        with self._uow_factory() as uow:
            scan = uow.scans.get(scan_id)
            if scan.is_terminal:
                return ExecuteScanOutcome.SKIPPED
            now = self._clock.now()
            if timed_out or scan.status is ScanStatus.QUEUED:
                code = reason.code if timed_out else ReasonCode.QUEUE_EXPIRED
                ended, event = scan.time_out(now, StatusReason(code))
            else:
                ended, event = scan.fail(now, reason)
            uow.scans.save(ended)
            uow.events.publish([event])
            uow.scan_logs.append(scan_id, LogLevel.ERROR, f"Scan ended: {reason.code.value}", now)
            uow.commit()
        self._queue.enqueue(ScanStep.REPORT, scan_id)
        if ended.status is ScanStatus.TIMED_OUT:
            return ExecuteScanOutcome.TIMED_OUT
        return ExecuteScanOutcome.FAILED

    def _log(self, scan_id: ScanId, level: LogLevel, message: str) -> None:
        with self._uow_factory() as uow:
            uow.scan_logs.append(scan_id, level, message, self._clock.now())
            uow.commit()
