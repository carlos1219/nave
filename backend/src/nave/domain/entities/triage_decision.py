from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Self

from nave.domain.errors import InvalidTriageDecisionError
from nave.domain.timestamps import ensure_utc
from nave.domain.value_objects.finding_text import Justification
from nave.domain.value_objects.identifiers import FindingId, TriageDecisionId, UserId
from nave.domain.value_objects.triage_state import TriageState

MAX_ACCEPTED_RISK_DURATION: Final = timedelta(days=365)
SYSTEM_STATES: Final = frozenset({TriageState.OPEN, TriageState.FIXED})


@dataclass(frozen=True, slots=True, kw_only=True)
class TriageDecision:
    """Decisión de triage: registro inmutable y append-only (CLAUDE.md §7, §12).

    `decided_by is None` significa decisión del sistema (FIXED al desaparecer un
    finding, OPEN al reaparecer). El sistema nunca acepta riesgos ni marca falsos
    positivos: esas decisiones exigen una persona y una justificación.
    """

    id: TriageDecisionId
    finding_id: FindingId
    state: TriageState
    decided_at: datetime
    decided_by: UserId | None
    justification: Justification | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        ensure_utc(self.decided_at, "decided_at")
        if self.expires_at is not None:
            ensure_utc(self.expires_at, "expires_at")
        if self.decided_by is None:
            self._check_system_decision()
        if self.state is TriageState.ACCEPTED_RISK:
            self._check_accepted_risk()
        elif self.expires_at is not None:
            raise InvalidTriageDecisionError("only ACCEPTED_RISK decisions can expire")
        if self.state is TriageState.FALSE_POSITIVE and self.justification is None:
            raise InvalidTriageDecisionError("FALSE_POSITIVE requires a justification")

    @classmethod
    def by_user(  # noqa: PLR0913 - campos obligatorios de la decisión
        cls,
        *,
        decision_id: TriageDecisionId,
        finding_id: FindingId,
        state: TriageState,
        user: UserId,
        at: datetime,
        justification: Justification | None,
        expires_at: datetime | None = None,
    ) -> Self:
        return cls(
            id=decision_id,
            finding_id=finding_id,
            state=state,
            decided_at=at,
            decided_by=user,
            justification=justification,
            expires_at=expires_at,
        )

    @classmethod
    def by_system(
        cls,
        *,
        decision_id: TriageDecisionId,
        finding_id: FindingId,
        state: TriageState,
        at: datetime,
        justification: Justification,
    ) -> Self:
        return cls(
            id=decision_id,
            finding_id=finding_id,
            state=state,
            decided_at=at,
            decided_by=None,
            justification=justification,
        )

    @property
    def is_system(self) -> bool:
        return self.decided_by is None

    def _check_system_decision(self) -> None:
        if self.state not in SYSTEM_STATES:
            raise InvalidTriageDecisionError("the system can only decide OPEN or FIXED")
        if self.justification is None:
            raise InvalidTriageDecisionError("system decisions require a justification")

    def _check_accepted_risk(self) -> None:
        if self.justification is None:
            raise InvalidTriageDecisionError("ACCEPTED_RISK requires a justification")
        if self.expires_at is None:
            raise InvalidTriageDecisionError("ACCEPTED_RISK requires an expiry date")
        if self.expires_at <= self.decided_at:
            raise InvalidTriageDecisionError("expires_at must be after decided_at")
        if self.expires_at - self.decided_at > MAX_ACCEPTED_RISK_DURATION:
            raise InvalidTriageDecisionError("accepted risk cannot exceed 365 days")
