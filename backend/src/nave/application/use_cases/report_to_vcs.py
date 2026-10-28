from dataclasses import dataclass
from enum import Enum

from nave.application.dto.vcs import VcsTarget
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.vcs import VcsReporterPort
from nave.application.services.pr_comment import MAX_LISTED_FINDINGS, build_pr_comment
from nave.domain.quality_gates import PolicyVerdict
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.scan_status import ScanStatus


@dataclass(frozen=True, slots=True)
class ReportToVcsCommand:
    scan_id: ScanId


class ReportOutcome(Enum):
    REPORTED = "REPORTED"
    SKIPPED = "SKIPPED"


class ReportToVcs:
    """Publica el resultado en GitHub: check run, SARIF y comentario en el PR.

    - Escaneos cancelados no se reportan: si se sustituyeron, reporta el nuevo.
    - FAILED y TIMED_OUT solo actualizan el check (para que el PR no quede pendiente).
    - Las lecturas se hacen en una transacción; las llamadas a GitHub, fuera de ella,
      para no mantener una transacción abierta durante E/S de red.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        vcs: VcsReporterPort,
        max_sarif_findings: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._vcs = vcs
        self._max_sarif = max_sarif_findings

    def execute(self, command: ReportToVcsCommand) -> ReportOutcome:
        with self._uow_factory() as uow:
            scan = uow.scans.get(command.scan_id)
            if not scan.is_terminal or scan.status is ScanStatus.CANCELLED:
                return ReportOutcome.SKIPPED
            repository = uow.tenants.get_repository(scan.repository_id)
            organization = uow.tenants.get_organization(repository.organization_id)
            target = VcsTarget(organization.github_installation_id, repository.ref)
            completed = scan.status is ScanStatus.COMPLETED
            all_views = (
                uow.findings.views_for_scan(scan.id, only_new=False, limit=self._max_sarif)
                if completed
                else ()
            )
            new_views = (
                uow.findings.views_for_scan(scan.id, only_new=True, limit=MAX_LISTED_FINDINGS)
                if completed and scan.pull_request_number is not None
                else ()
            )
            verdict = uow.policies.get_evaluation(scan.id) if completed else None
        self._vcs.publish_check(target, scan)
        if not completed or scan.summary is None:
            return ReportOutcome.REPORTED
        self._vcs.upload_sarif(target, scan, all_views)
        if scan.pull_request_number is not None:
            body = build_pr_comment(
                summary=scan.summary,
                verdict=verdict or PolicyVerdict(scan.summary.verdict),
                new_findings=new_views,
            )
            self._vcs.upsert_pr_comment(target, scan, body)
        return ReportOutcome.REPORTED
