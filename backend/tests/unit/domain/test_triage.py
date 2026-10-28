from dataclasses import replace
from datetime import timedelta

import pytest

from nave.domain.entities.finding import Finding
from nave.domain.entities.triage_decision import TriageDecision
from nave.domain.errors import (
    FindingInvariantError,
    InvalidTimestampError,
    InvalidTriageDecisionError,
    RedundantTriageDecisionError,
)
from nave.domain.value_objects.finding_text import FindingTitle
from nave.domain.value_objects.identifiers import FindingId
from nave.domain.value_objects.rule_id import CveId, CweId
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.triage_state import TriageState as State
from tests.unit.domain.builders import (
    OTHER_SCAN_ID,
    T0,
    at,
    new_finding,
    system_decision,
    user_decision,
)

DAY = timedelta(days=1)


class TestDecisionRules:
    def test_accepted_risk_requires_justification_and_expiry(self) -> None:
        with pytest.raises(InvalidTriageDecisionError):
            user_decision(State.ACCEPTED_RISK, justification=None, expires_in=DAY)
        with pytest.raises(InvalidTriageDecisionError):
            user_decision(State.ACCEPTED_RISK)

    @pytest.mark.parametrize("expires_in", [timedelta(0), -DAY, timedelta(days=366)])
    def test_accepted_risk_expiry_window(self, expires_in: timedelta) -> None:
        with pytest.raises(InvalidTriageDecisionError):
            user_decision(State.ACCEPTED_RISK, expires_in=expires_in)

    def test_accepted_risk_at_maximum_duration(self) -> None:
        decision = user_decision(State.ACCEPTED_RISK, expires_in=timedelta(days=365))
        assert decision.expires_at is not None

    def test_false_positive_requires_justification(self) -> None:
        with pytest.raises(InvalidTriageDecisionError):
            user_decision(State.FALSE_POSITIVE, justification=None)

    def test_only_accepted_risk_expires(self) -> None:
        with pytest.raises(InvalidTriageDecisionError):
            user_decision(State.FALSE_POSITIVE, expires_in=DAY)

    def test_open_may_omit_justification(self) -> None:
        assert user_decision(State.OPEN, justification=None).justification is None

    @pytest.mark.parametrize("state", [State.ACCEPTED_RISK, State.FALSE_POSITIVE])
    def test_system_cannot_take_human_decisions(self, state: State) -> None:
        with pytest.raises(InvalidTriageDecisionError):
            system_decision(state)

    def test_system_decision_requires_justification(self) -> None:
        decision = system_decision(State.FIXED)
        assert decision.is_system
        with pytest.raises(InvalidTriageDecisionError):
            replace(decision, justification=None)

    def test_requires_utc(self) -> None:
        decision = user_decision(State.OPEN)
        with pytest.raises(InvalidTimestampError):
            replace(decision, decided_at=decision.decided_at.replace(tzinfo=None))


class TestFinding:
    def test_discover_initial_state(self) -> None:
        finding = new_finding()
        assert finding.triage_state is State.OPEN
        assert finding.first_seen_at == finding.last_seen_at == T0

    def test_observe_updates_last_seen_and_rule_data(self) -> None:
        finding = new_finding().observe(
            scan_id=OTHER_SCAN_ID, at=at(5), severity=Severity.CRITICAL, title=FindingTitle("New")
        )
        assert finding.last_seen_scan_id == OTHER_SCAN_ID
        assert finding.severity is Severity.CRITICAL
        assert finding.first_seen_at == T0

    def test_observe_cannot_go_back_in_time(self) -> None:
        finding = new_finding().observe(
            scan_id=OTHER_SCAN_ID, at=at(5), severity=Severity.HIGH, title=FindingTitle("x")
        )
        with pytest.raises(FindingInvariantError):
            finding.observe(
                scan_id=OTHER_SCAN_ID, at=at(1), severity=Severity.HIGH, title=FindingTitle("x")
            )

    def test_accept_risk_then_expiry_reopens_effectively(self) -> None:
        decision = user_decision(State.ACCEPTED_RISK, expires_in=DAY)
        accepted, event = new_finding().apply_triage(decision)
        assert accepted.triage_state is State.ACCEPTED_RISK
        assert (event.previous, event.current, event.by_system) == (
            State.OPEN,
            State.ACCEPTED_RISK,
            False,
        )
        assert accepted.effective_triage_state(at(11)) is State.ACCEPTED_RISK
        assert accepted.effective_triage_state(at(10) + DAY) is State.OPEN

    def test_renewing_accepted_risk_is_allowed(self) -> None:
        accepted, _ = new_finding().apply_triage(user_decision(State.ACCEPTED_RISK, expires_in=DAY))
        renewed, _ = accepted.apply_triage(
            user_decision(State.ACCEPTED_RISK, minutes=20, expires_in=2 * DAY)
        )
        assert renewed.triage_expires_at == at(20) + 2 * DAY

    def test_redundant_human_decision_is_rejected(self) -> None:
        with pytest.raises(RedundantTriageDecisionError):
            new_finding().apply_triage(user_decision(State.OPEN))

    def test_leaving_accepted_risk_clears_expiry(self) -> None:
        accepted, _ = new_finding().apply_triage(user_decision(State.ACCEPTED_RISK, expires_in=DAY))
        fp, _ = accepted.apply_triage(user_decision(State.FALSE_POSITIVE, minutes=20))
        assert fp.triage_expires_at is None

    def test_system_fixes_open_and_reopens_regressions(self) -> None:
        fixed, event = new_finding().apply_triage(system_decision(State.FIXED))
        assert fixed.triage_state is State.FIXED
        assert event.by_system
        reopened, _ = fixed.apply_triage(system_decision(State.OPEN, minutes=20))
        assert reopened.triage_state is State.OPEN

    def test_system_never_overrides_a_false_positive(self) -> None:
        fp, _ = new_finding().apply_triage(user_decision(State.FALSE_POSITIVE))
        with pytest.raises(RedundantTriageDecisionError):
            fp.apply_triage(system_decision(State.FIXED, minutes=20))

    def test_system_may_fix_an_expired_accepted_risk(self) -> None:
        accepted, _ = new_finding().apply_triage(user_decision(State.ACCEPTED_RISK, expires_in=DAY))
        fixed, _ = accepted.apply_triage(system_decision(State.FIXED, minutes=60 * 48))
        assert fixed.triage_state is State.FIXED

    def test_decision_for_another_finding(self) -> None:
        decision = replace(
            user_decision(State.FALSE_POSITIVE),
            finding_id=FindingId(new_finding().repository_id.value),
        )
        with pytest.raises(FindingInvariantError):
            new_finding().apply_triage(decision)

    def test_decision_before_discovery(self) -> None:
        with pytest.raises(FindingInvariantError):
            new_finding().apply_triage(user_decision(State.FALSE_POSITIVE, minutes=-1))


class TestFindingInvariants:
    def test_accepted_risk_needs_expiry(self) -> None:
        with pytest.raises(FindingInvariantError):
            replace(new_finding(), triage_state=State.ACCEPTED_RISK)

    def test_expiry_only_for_accepted_risk(self) -> None:
        with pytest.raises(FindingInvariantError):
            replace(new_finding(), triage_expires_at=at(5))

    def test_last_seen_before_first_seen(self) -> None:
        with pytest.raises(FindingInvariantError):
            replace(new_finding(), last_seen_at=T0 - DAY)

    @pytest.mark.parametrize(
        "cwe",
        [
            [CweId("CWE-1")],
            (CveId("CVE-2024-3094"),),
            (CweId("CWE-1"), CweId("CWE-1")),
            tuple(CweId(f"CWE-{n}") for n in range(1, 34)),
        ],
    )
    def test_invalid_references(self, cwe: tuple[CweId, ...]) -> None:
        with pytest.raises(FindingInvariantError):
            replace(new_finding(), cwe=cwe)

    @pytest.mark.parametrize("version", [-1, True])
    def test_invalid_version(self, version: int) -> None:
        with pytest.raises(FindingInvariantError):
            replace(new_finding(), version=version)

    def test_non_utc_expiry(self) -> None:
        accepted, _ = new_finding().apply_triage(user_decision(State.ACCEPTED_RISK, expires_in=DAY))
        with pytest.raises(InvalidTimestampError):
            replace(accepted, triage_expires_at=at(100).replace(tzinfo=None))

    def test_decision_dataclass_is_reexported(self) -> None:
        assert TriageDecision.by_user is not None
        assert Finding.discover is not None


class TestSystemNeverOverridesHumans:
    def test_system_cannot_fix_a_live_accepted_risk(self) -> None:
        accepted, _ = new_finding().apply_triage(user_decision(State.ACCEPTED_RISK, expires_in=DAY))
        with pytest.raises(RedundantTriageDecisionError):
            accepted.apply_triage(system_decision(State.FIXED, minutes=20))

    def test_system_reopens_only_fixed_findings(self) -> None:
        with pytest.raises(RedundantTriageDecisionError):
            new_finding().apply_triage(system_decision(State.OPEN))

    def test_title_must_be_validated_text(self) -> None:
        with pytest.raises(FindingInvariantError):
            replace(new_finding(), title="raw string")  # type: ignore[arg-type]

    def test_cve_references_are_checked_too(self) -> None:
        with pytest.raises(FindingInvariantError):
            replace(new_finding(), cve=(CweId("CWE-79"),))  # type: ignore[arg-type]
