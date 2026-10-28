from collections.abc import Sequence
from datetime import datetime

from nave.domain.quality_gates import (
    FindingSnapshot,
    PolicyVerdict,
    QualityGatePolicy,
    evaluate_quality_gates,
)


class QualityGateEvaluator:
    """`PolicyEvaluatorPort` (ADR 0003): delega en las reglas puras del dominio. Existe
    como adaptador para poder sustituir el motor sin tocar los casos de uso."""

    def evaluate(
        self, policy: QualityGatePolicy, findings: Sequence[FindingSnapshot], now: datetime
    ) -> PolicyVerdict:
        return evaluate_quality_gates(policy, findings, now)
