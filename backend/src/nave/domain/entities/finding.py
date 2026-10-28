from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Self

from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.errors import FindingInvariantError, RedundantTriageDecisionError
from nave.domain.events import FindingTriaged
from nave.domain.timestamps import ensure_utc
from nave.domain.validation import is_int_in_range
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import FindingTitle
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.identifiers import FindingId, RepositoryId, ScanId
from nave.domain.value_objects.rule_id import CveId, CweId, RuleId
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.triage_state import TriageState

MAX_REFERENCES: Final = 32
MAX_VERSION: Final = 2**63 - 1


def _check_references(values: object, kind: type[CweId] | type[CveId], field: str) -> None:
    if not isinstance(values, tuple):
        raise FindingInvariantError(f"{field} must be a tuple")
    if len(values) > MAX_REFERENCES:
        raise FindingInvariantError(f"{field} must not exceed {MAX_REFERENCES} entries")
    if not all(isinstance(value, kind) for value in values):
        raise FindingInvariantError(f"{field} must contain only {kind.__name__}")
    if len(set(values)) != len(values):
        raise FindingInvariantError(f"{field} must not contain duplicates")


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """Identidad estable de un hallazgo dentro de un repositorio (por fingerprint).

    Guarda el estado de triage vigente; el historial completo son las `TriageDecision`
    append-only. El estado *efectivo* depende de la hora: una aceptación de riesgo
    caducada vuelve a contar como `OPEN` (ver `effective_triage_state`).
    """

    id: FindingId
    repository_id: RepositoryId
    fingerprint: Fingerprint
    scanner_kind: ScannerKind
    category: FindingCategory
    rule_id: RuleId
    severity: Severity
    title: FindingTitle
    first_seen_scan_id: ScanId
    last_seen_scan_id: ScanId
    first_seen_at: datetime
    last_seen_at: datetime
    cwe: tuple[CweId, ...] = ()
    cve: tuple[CveId, ...] = ()
    triage_state: TriageState = TriageState.OPEN
    triage_expires_at: datetime | None = None
    version: int = 0

    def __post_init__(self) -> None:
        ensure_utc(self.first_seen_at, "first_seen_at")
        ensure_utc(self.last_seen_at, "last_seen_at")
        if self.last_seen_at < self.first_seen_at:
            raise FindingInvariantError("last_seen_at precedes first_seen_at")
        _check_references(self.cwe, CweId, "cwe")
        _check_references(self.cve, CveId, "cve")
        if not isinstance(self.title, FindingTitle):
            raise FindingInvariantError("title must be a FindingTitle")
        if (self.triage_state is TriageState.ACCEPTED_RISK) != (self.triage_expires_at is not None):
            raise FindingInvariantError("triage_expires_at must be set exactly for ACCEPTED_RISK")
        if self.triage_expires_at is not None:
            ensure_utc(self.triage_expires_at, "triage_expires_at")
        if not is_int_in_range(self.version, 0, MAX_VERSION):
            raise FindingInvariantError("version must be a non-negative integer")

    @classmethod
    def discover(  # noqa: PLR0913 - atributos obligatorios del hallazgo
        cls,
        *,
        finding_id: FindingId,
        repository_id: RepositoryId,
        fingerprint: Fingerprint,
        scanner_kind: ScannerKind,
        category: FindingCategory,
        rule_id: RuleId,
        severity: Severity,
        title: FindingTitle,
        scan_id: ScanId,
        at: datetime,
        cwe: tuple[CweId, ...] = (),
        cve: tuple[CveId, ...] = (),
    ) -> Self:
        return cls(
            id=finding_id,
            repository_id=repository_id,
            fingerprint=fingerprint,
            scanner_kind=scanner_kind,
            category=category,
            rule_id=rule_id,
            severity=severity,
            title=title,
            first_seen_scan_id=scan_id,
            last_seen_scan_id=scan_id,
            first_seen_at=at,
            last_seen_at=at,
            cwe=cwe,
            cve=cve,
        )

    def effective_triage_state(self, now: datetime) -> TriageState:
        """Estado que cuenta para quality gates, SLAs y detección de corregidos.

        La caducidad obligatoria de una aceptación de riesgo no serviría de nada si no se
        aplicara aquí: al llegar `triage_expires_at` el hallazgo vuelve a estar abierto.
        """
        ensure_utc(now, "now")
        if self.triage_expires_at is not None and now >= self.triage_expires_at:
            return TriageState.OPEN
        return self.triage_state

    def observe(
        self, *, scan_id: ScanId, at: datetime, severity: Severity, title: FindingTitle
    ) -> Self:
        """Un escaneo ha vuelto a reportar el hallazgo.

        La severidad y el título se actualizan: la regla puede haber cambiado. La
        reapertura de un hallazgo `FIXED` es una decisión de triage aparte (sistema),
        para que quede en el historial auditado.
        """
        ensure_utc(at, "at")
        if at < self.last_seen_at:
            raise FindingInvariantError("observations must not go back in time")
        return replace(
            self, last_seen_scan_id=scan_id, last_seen_at=at, severity=severity, title=title
        )

    def apply_triage(self, decision: TriageDecision) -> tuple[Self, FindingTriaged]:
        if decision.finding_id != self.id:
            raise FindingInvariantError("the decision belongs to another finding")
        if decision.decided_at < self.first_seen_at:
            raise FindingInvariantError("a decision cannot precede the finding's discovery")
        self._reject_redundant(decision)
        updated = replace(self, triage_state=decision.state, triage_expires_at=decision.expires_at)
        event = FindingTriaged(
            occurred_at=decision.decided_at,
            finding_id=self.id,
            decision_id=decision.id,
            previous=self.triage_state,
            current=decision.state,
            by_system=decision.is_system,
        )
        return updated, event

    def _reject_redundant(self, decision: TriageDecision) -> None:
        effective = self.effective_triage_state(decision.decided_at)
        if decision.is_system:
            # El sistema solo corrige lo abierto y reabre lo corregido. Nunca pisa una
            # decisión humana vigente (falso positivo o riesgo aceptado no caducado).
            allowed = (
                effective is TriageState.OPEN
                if decision.state is TriageState.FIXED
                else self.triage_state is TriageState.FIXED
            )
            if not allowed:
                raise RedundantTriageDecisionError(
                    f"the system cannot move a {effective.value} finding to {decision.state.value}"
                )
            return
        # Renovar una aceptación de riesgo (nueva caducidad) no es redundante.
        if decision.state is effective and decision.state is not TriageState.ACCEPTED_RISK:
            raise RedundantTriageDecisionError(f"the finding is already {effective.value}")
