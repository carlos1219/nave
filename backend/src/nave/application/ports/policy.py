from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from nave.domain.quality_gates import FindingSnapshot, PolicyVerdict, QualityGatePolicy


class PolicyEvaluatorPort(Protocol):
    def evaluate(
        self, policy: QualityGatePolicy, findings: Sequence[FindingSnapshot], now: datetime
    ) -> PolicyVerdict:
        """Evalúa los quality gates. Debe ser determinista y sin efectos (ADR 0003)."""
        ...
