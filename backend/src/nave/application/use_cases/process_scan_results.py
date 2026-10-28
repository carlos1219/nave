from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from nave.application.dto.scanning import ScannerOutput
from nave.application.dto.views import LogLevel
from nave.application.ports.messaging import ScanQueuePort, ScanStep
from nave.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from nave.application.ports.policy import PolicyEvaluatorPort
from nave.application.ports.system import Clock, IdGenerator
from nave.application.services.baseline import diff_against_baseline
from nave.application.services.fingerprinting import FingerprintKey
from nave.application.services.normalizer import NormalizedFinding, normalize_output
from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.events import DomainEvent
from nave.domain.quality_gates import FindingSnapshot, PolicyVerdict, Verdict
from nave.domain.value_objects.finding_text import Justification
from nave.domain.value_objects.identifiers import (
    FindingId,
    OrganizationId,
    ScanId,
    TriageDecisionId,
)
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.tool_run import ToolRun
from nave.domain.value_objects.triage_state import TriageState


@dataclass(frozen=True, slots=True)
class ProcessScanResultsCommand:
    scan_id: ScanId
    outputs: tuple[ScannerOutput, ...]


class ProcessScanResults:
    """Normaliza, persiste findings y ocurrencias, aplica el triage del sistema, evalúa
    los quality gates y completa el escaneo, todo en una transacción.

    Reglas de seguridad de esta etapa:
    - El triage automático (FIXED al desaparecer, OPEN al reaparecer) solo se aplica en
      la rama por defecto y con resultados completos: que un finding falte en un
      resultado truncado o parcial no prueba que esté corregido.
    - Para los quality gates, un finding presente en este escaneo cuenta como abierto
      aunque figure como FIXED: un PR que reintroduce una vulnerabilidad corregida no
      debe pasar el gate.
    - Unos resultados incompletos nunca producen PASS.
    """

    def __init__(  # noqa: PLR0913 - dependencias del caso de uso
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        queue: ScanQueuePort,
        evaluator: PolicyEvaluatorPort,
        clock: Clock,
        ids: IdGenerator,
        max_findings_per_scanner: int,
        fingerprint_key: FingerprintKey,
        fix_page_size: int = 500,
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._evaluator = evaluator
        self._clock = clock
        self._ids = ids
        self._max_findings = max_findings_per_scanner
        self._fingerprint_key = fingerprint_key
        self._fix_page_size = fix_page_size

    def execute(self, command: ProcessScanResultsCommand) -> Scan:
        with self._uow_factory() as uow:
            scan = uow.scans.get(command.scan_id)
            if scan.is_terminal:
                return scan
            if scan.status is not ScanStatus.PROCESSING:
                scan, event = scan.start_processing(self._clock.now())
                scan = uow.scans.save(scan)
                uow.events.publish([event])
            scan = self._process(uow, scan, command.outputs)
            uow.commit()
        self._queue.enqueue(ScanStep.REPORT, scan.id)
        return scan

    def _process(self, uow: UnitOfWork, scan: Scan, outputs: Sequence[ScannerOutput]) -> Scan:
        now = self._clock.now()
        repository = uow.tenants.get_repository(scan.repository_id)
        results = [
            normalize_output(o, max_findings=self._max_findings, secret_key=self._fingerprint_key)
            for o in outputs
        ]
        incomplete = any(r.incomplete for r in results)
        for output, result in zip(outputs, results, strict=True):
            uow.scan_logs.append(
                scan.id,
                LogLevel.WARNING if result.incomplete else LogLevel.INFO,
                f"{output.kind.value} {output.scanner_version}: {len(result.findings)} findings, "
                f"{result.rejected} rejected, truncated={str(result.truncated).lower()}",
                now,
            )
        items = [finding for result in results for finding in result.findings]
        fingerprints = [item.fingerprint for item in items]
        existing = uow.findings.find_by_fingerprints(repository.id, fingerprints)
        baseline = uow.findings.baseline_fingerprints(repository.id)
        new = diff_against_baseline(fingerprints, baseline).new
        on_default_branch = repository.is_default_branch(scan.ref)
        events: list[DomainEvent] = []
        occurrences: list[FindingOccurrence] = []
        snapshots: list[FindingSnapshot] = []
        for item in items:
            finding = self._upsert(uow, scan, item, existing.get(item.fingerprint), now)
            if on_default_branch and finding.triage_state is TriageState.FIXED:
                why = f"Reappeared in scan {scan.id}."
                finding = self._system_triage(
                    uow, finding, state=TriageState.OPEN, why=why, now=now, events=events
                )
            is_new = item.fingerprint in new
            occurrences.append(self._occurrence(scan, finding.id, item, is_new=is_new))
            state = finding.effective_triage_state(now)
            snapshots.append(
                FindingSnapshot(
                    severity=item.severity,
                    effective_state=TriageState.OPEN if state is TriageState.FIXED else state,
                    first_seen_at=finding.first_seen_at,
                    is_new=is_new,
                )
            )
        uow.findings.record_occurrences(occurrences)
        if on_default_branch and not incomplete:
            self._fix_missing(uow, scan, now, events)
        policy_id, verdict = self._evaluate(
            uow, repository.organization_id, snapshots, now, incomplete=incomplete
        )
        uow.policies.record_evaluation(scan.id, policy_id, verdict)
        open_snapshots = [s for s in snapshots if s.effective_state is TriageState.OPEN]
        summary = ScanSummary(
            new=SeverityCounts.of(s.severity for s in open_snapshots if s.is_new),
            open=SeverityCounts.of(s.severity for s in open_snapshots),
            verdict=verdict.verdict,
            truncated=incomplete,
            tools=tuple(
                ToolRun(
                    scanner=output.kind,
                    version=output.scanner_version,
                    image_digest=output.image_digest,
                    findings=len(result.findings),
                )
                for output, result in zip(outputs, results, strict=True)
            ),
        )
        completed, event = scan.complete(now, summary)
        uow.events.publish([*events, event])
        uow.scan_logs.append(
            scan.id, LogLevel.INFO, f"Scan completed: {summary.verdict.value}", now
        )
        return uow.scans.save(completed)

    def _upsert(
        self,
        uow: UnitOfWork,
        scan: Scan,
        item: NormalizedFinding,
        current: Finding | None,
        now: datetime,
    ) -> Finding:
        if current is None:
            finding = Finding.discover(
                finding_id=FindingId(self._ids.new_uuid()),
                repository_id=scan.repository_id,
                fingerprint=item.fingerprint,
                scanner_kind=item.scanner_kind,
                category=item.category,
                rule_id=item.rule_id,
                severity=item.severity,
                title=item.title,
                scan_id=scan.id,
                at=now,
                cwe=item.cwe,
                cve=item.cve,
            )
            uow.findings.add(finding)
            return finding
        observed = current.observe(
            scan_id=scan.id, at=now, severity=item.severity, title=item.title
        )
        return uow.findings.save(observed)

    def _system_triage(  # noqa: PLR0913 - contexto de la decisión del sistema
        self,
        uow: UnitOfWork,
        finding: Finding,
        *,
        state: TriageState,
        why: str,
        now: datetime,
        events: list[DomainEvent],
    ) -> Finding:
        decision = TriageDecision.by_system(
            decision_id=TriageDecisionId(self._ids.new_uuid()),
            finding_id=finding.id,
            state=state,
            at=now,
            justification=Justification(why),
        )
        updated, event = finding.apply_triage(decision)
        uow.findings.add_decision(decision)
        events.append(event)
        return uow.findings.save(updated)

    def _fix_missing(
        self, uow: UnitOfWork, scan: Scan, now: datetime, events: list[DomainEvent]
    ) -> None:
        # Por páginas con cursor por ID: el triage no cambia `last_seen_scan_id`, así que
        # el cursor es estable y la memoria queda acotada (AM-309).
        after: FindingId | None = None
        while True:
            page = uow.findings.list_not_seen_in(
                scan.repository_id, scan.id, after=after, limit=self._fix_page_size
            )
            for finding in page:
                if finding.effective_triage_state(now) is TriageState.OPEN:
                    why = f"Not observed in scan {scan.id}."
                    self._system_triage(
                        uow, finding, state=TriageState.FIXED, why=why, now=now, events=events
                    )
            if len(page) < self._fix_page_size:
                return
            after = page[-1].id

    def _evaluate(
        self,
        uow: UnitOfWork,
        organization_id: OrganizationId,
        snapshots: Sequence[FindingSnapshot],
        now: datetime,
        *,
        incomplete: bool,
    ) -> tuple[str | None, PolicyVerdict]:
        record = uow.policies.active_policy(organization_id)
        if record is None:
            policy_id, verdict = None, PolicyVerdict(Verdict.NEUTRAL)
        else:
            policy_id = record.policy_id
            verdict = self._evaluator.evaluate(record.policy, snapshots, now)
        if incomplete:
            verdict = verdict.downgraded_for_truncation()
        return policy_id, verdict

    @staticmethod
    def _occurrence(
        scan: Scan, finding_id: FindingId, item: NormalizedFinding, *, is_new: bool
    ) -> FindingOccurrence:
        return FindingOccurrence(
            scan_id=scan.id,
            finding_id=finding_id,
            location=item.location,
            message=item.message,
            snippet=item.snippet,
            is_new=is_new,
        )
