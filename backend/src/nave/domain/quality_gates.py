"""Quality gates declarativos (ADR 0003). Funciones puras, sin E/S."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Final, Self

from nave.domain.errors import InvalidQualityGatePolicyError
from nave.domain.timestamps import ensure_utc
from nave.domain.validation import fullmatch_str, is_int_in_range
from nave.domain.value_objects.enum_parsing import parse_enum
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.triage_state import TriageState

MAX_GATES: Final = 32
MAX_LIMIT: Final = 1_000_000
MAX_SLA_DAYS: Final = 3650
TRUNCATED_RULE: Final = "results-truncated"
SLA_RULE: Final = "sla"
_GATE_ID: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,62}", re.ASCII)


class GateScope(Enum):
    NEW_FINDINGS = "NEW_FINDINGS"
    ALL_OPEN = "ALL_OPEN"

    @classmethod
    def parse(cls, raw: str) -> Self:
        # El documento de política (YAML) usa minúsculas: `scope: new_findings`.
        text: object = raw
        return parse_enum(cls, text.upper() if isinstance(text, str) else text, "scope")


class Verdict(Enum):
    """Se traduce a la conclusión del Check de GitHub: success / failure / neutral."""

    PASS = "PASS"  # noqa: S105 - es un veredicto, no una contraseña
    FAIL = "FAIL"
    NEUTRAL = "NEUTRAL"

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "verdict")


class ReasonKind(Enum):
    GATE = "GATE"
    SLA = "SLA"
    TRUNCATED = "TRUNCATED"


def _frozen_limits(raw: object, field: str, minimum: int) -> Mapping[Severity, int]:
    if not isinstance(raw, Mapping):
        raise InvalidQualityGatePolicyError(field, "must be a mapping")
    for severity, value in raw.items():
        if not isinstance(severity, Severity):
            raise InvalidQualityGatePolicyError(field, "keys must be severities")
        if not is_int_in_range(value, minimum, MAX_LIMIT):
            raise InvalidQualityGatePolicyError(field, f"values must be integers >= {minimum}")
    # Copia de solo lectura: quien construyó la política no puede alterarla después.
    return MappingProxyType(dict(raw))


@dataclass(frozen=True, slots=True)
class Gate:
    """Máximo de hallazgos abiertos por severidad dentro de un ámbito."""

    id: str
    scope: GateScope
    max_by_severity: Mapping[Severity, int]

    def __post_init__(self) -> None:
        if not fullmatch_str(_GATE_ID, self.id):
            raise InvalidQualityGatePolicyError("gate.id", "must be a lowercase slug")
        if not isinstance(self.scope, GateScope):
            raise InvalidQualityGatePolicyError("gate.scope", "must be a GateScope")
        limits = _frozen_limits(self.max_by_severity, "gate.max", 0)
        if not limits:
            raise InvalidQualityGatePolicyError("gate.max", "must define at least one severity")
        object.__setattr__(self, "max_by_severity", limits)


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityGatePolicy:
    version: int
    gates: tuple[Gate, ...]
    sla_days: Mapping[Severity, int]
    fail_on_sla_breach: bool

    def __post_init__(self) -> None:
        if not is_int_in_range(self.version, 1, 1):
            raise InvalidQualityGatePolicyError("version", "only version 1 is supported")
        gates: object = self.gates
        if not isinstance(gates, tuple) or not all(isinstance(g, Gate) for g in gates):
            raise InvalidQualityGatePolicyError("gates", "must be a tuple of Gate")
        if len(self.gates) > MAX_GATES:
            raise InvalidQualityGatePolicyError("gates", f"must not exceed {MAX_GATES}")
        if len({gate.id for gate in self.gates}) != len(self.gates):
            raise InvalidQualityGatePolicyError("gates", "gate ids must be unique")
        if not isinstance(self.fail_on_sla_breach, bool):
            raise InvalidQualityGatePolicyError("fail_on_sla_breach", "must be a boolean")
        sla = _frozen_limits(self.sla_days, "sla_days", 1)
        if any(days > MAX_SLA_DAYS for days in sla.values()):
            raise InvalidQualityGatePolicyError("sla_days", f"must not exceed {MAX_SLA_DAYS}")
        object.__setattr__(self, "sla_days", sla)

    @property
    def is_empty(self) -> bool:
        return not self.gates and not self.sla_days


@dataclass(frozen=True, slots=True)
class FindingSnapshot:
    """Lo mínimo de un hallazgo que la política necesita, con el triage ya efectivo."""

    severity: Severity
    effective_state: TriageState
    first_seen_at: datetime
    is_new: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class VerdictReason:
    """Motivo estructurado: el texto final lo compone cada canal (Check, comentario, UI)."""

    kind: ReasonKind
    rule: str
    blocking: bool
    severity: Severity | None = None
    observed: int = 0
    limit: int = 0


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    verdict: Verdict
    reasons: tuple[VerdictReason, ...] = ()

    def downgraded_for_truncation(self) -> "PolicyVerdict":
        """Con resultados incompletos un PASS no es fiable: pasa a NEUTRAL. Un FAIL se queda."""
        reason = VerdictReason(kind=ReasonKind.TRUNCATED, rule=TRUNCATED_RULE, blocking=False)
        verdict = Verdict.FAIL if self.verdict is Verdict.FAIL else Verdict.NEUTRAL
        return PolicyVerdict(verdict, (*self.reasons, reason))


def evaluate_quality_gates(
    policy: QualityGatePolicy, findings: Iterable[FindingSnapshot], now: datetime
) -> PolicyVerdict:
    """Evalúa la política. Solo cuentan los hallazgos con triage efectivo `OPEN`.

    - Gate incumplido → `FAIL`.
    - SLA superado (antigüedad estrictamente mayor que el plazo) → motivo bloqueante si
      `fail_on_sla_breach`; si no, informativo y el veredicto no cambia.
    - Política vacía → `NEUTRAL`: no hay nada que garantizar.
    - Los motivos salen en orden determinista para que el Check no cambie sin motivo.
    """
    ensure_utc(now, "now")
    open_findings = [f for f in findings if f.effective_state is TriageState.OPEN]
    if policy.is_empty:
        return PolicyVerdict(Verdict.NEUTRAL)
    counts = {
        GateScope.ALL_OPEN: Counter(f.severity for f in open_findings),
        GateScope.NEW_FINDINGS: Counter(f.severity for f in open_findings if f.is_new),
    }
    reasons: list[VerdictReason] = []
    for gate in policy.gates:
        for severity in sorted(gate.max_by_severity, reverse=True):
            limit = gate.max_by_severity[severity]
            observed = counts[gate.scope][severity]
            if observed > limit:
                reasons.append(
                    VerdictReason(
                        kind=ReasonKind.GATE,
                        rule=gate.id,
                        blocking=True,
                        severity=severity,
                        observed=observed,
                        limit=limit,
                    )
                )
    for severity in sorted(policy.sla_days, reverse=True):
        days = policy.sla_days[severity]
        deadline = timedelta(days=days)
        overdue = sum(
            1 for f in open_findings if f.severity is severity and now - f.first_seen_at > deadline
        )
        if overdue:
            reasons.append(
                VerdictReason(
                    kind=ReasonKind.SLA,
                    rule=SLA_RULE,
                    blocking=policy.fail_on_sla_breach,
                    severity=severity,
                    observed=overdue,
                    limit=days,
                )
            )
    verdict = Verdict.FAIL if any(r.blocking for r in reasons) else Verdict.PASS
    return PolicyVerdict(verdict, tuple(reasons))
