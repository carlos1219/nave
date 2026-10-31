from datetime import timedelta

import pytest

from nave.domain.errors import InvalidQualityGatePolicyError, InvalidTimestampError
from nave.domain.quality_gates import (
    FindingSnapshot,
    Gate,
    GateScope,
    QualityGatePolicy,
    ReasonKind,
    Verdict,
    default_policy,
    evaluate_quality_gates,
)
from nave.domain.value_objects.severity import Severity as Sev
from nave.domain.value_objects.triage_state import TriageState as State
from tests.unit.domain.builders import T0, at

NOW = T0 + timedelta(days=40)

POLICY = QualityGatePolicy(
    version=1,
    gates=(
        Gate("no-new-critical-high", GateScope.NEW_FINDINGS, {Sev.CRITICAL: 0, Sev.HIGH: 0}),
        Gate("medium-budget", GateScope.ALL_OPEN, {Sev.MEDIUM: 1}),
    ),
    sla_days={Sev.CRITICAL: 7, Sev.HIGH: 30},
    fail_on_sla_breach=True,
)


def snap(
    severity: Sev, *, new: bool = False, state: State = State.OPEN, age_days: int = 0
) -> FindingSnapshot:
    return FindingSnapshot(
        severity=severity,
        effective_state=state,
        first_seen_at=NOW - timedelta(days=age_days),
        is_new=new,
    )


def test_pass_when_within_limits() -> None:
    verdict = evaluate_quality_gates(POLICY, [snap(Sev.HIGH, age_days=1), snap(Sev.MEDIUM)], NOW)
    assert verdict.verdict is Verdict.PASS
    assert verdict.reasons == ()


def test_new_high_fails_with_reason() -> None:
    verdict = evaluate_quality_gates(POLICY, [snap(Sev.HIGH, new=True)], NOW)
    assert verdict.verdict is Verdict.FAIL
    (reason,) = verdict.reasons
    assert (reason.kind, reason.rule, reason.severity, reason.observed, reason.limit) == (
        ReasonKind.GATE,
        "no-new-critical-high",
        Sev.HIGH,
        1,
        0,
    )


def test_all_open_scope_counts_old_findings() -> None:
    verdict = evaluate_quality_gates(POLICY, [snap(Sev.MEDIUM), snap(Sev.MEDIUM)], NOW)
    assert verdict.verdict is Verdict.FAIL


@pytest.mark.parametrize("state", [State.FALSE_POSITIVE, State.ACCEPTED_RISK, State.FIXED])
def test_non_open_findings_do_not_count(state: State) -> None:
    findings = [snap(Sev.CRITICAL, new=True, state=state, age_days=100)]
    assert evaluate_quality_gates(POLICY, findings, NOW).verdict is Verdict.PASS


def test_sla_breach_is_blocking_when_configured() -> None:
    verdict = evaluate_quality_gates(POLICY, [snap(Sev.HIGH, age_days=31)], NOW)
    assert verdict.verdict is Verdict.FAIL
    assert verdict.reasons[0].kind is ReasonKind.SLA


def test_sla_breach_is_informative_otherwise() -> None:
    policy = QualityGatePolicy(
        version=1, gates=(), sla_days={Sev.HIGH: 30}, fail_on_sla_breach=False
    )
    verdict = evaluate_quality_gates(policy, [snap(Sev.HIGH, age_days=31)], NOW)
    assert verdict.verdict is Verdict.PASS
    assert not verdict.reasons[0].blocking


def test_sla_boundary_is_not_a_breach() -> None:
    assert (
        evaluate_quality_gates(POLICY, [snap(Sev.HIGH, age_days=30)], NOW).verdict is Verdict.PASS
    )


def test_empty_policy_is_neutral() -> None:
    policy = QualityGatePolicy(version=1, gates=(), sla_days={}, fail_on_sla_breach=False)
    assert evaluate_quality_gates(policy, [snap(Sev.CRITICAL)], NOW).verdict is Verdict.NEUTRAL


def test_truncation_downgrades_pass_but_keeps_fail() -> None:
    passed = evaluate_quality_gates(POLICY, [], NOW).downgraded_for_truncation()
    assert passed.verdict is Verdict.NEUTRAL
    assert passed.reasons[-1].kind is ReasonKind.TRUNCATED
    failed = evaluate_quality_gates(POLICY, [snap(Sev.HIGH, new=True)], NOW)
    assert failed.downgraded_for_truncation().verdict is Verdict.FAIL


def test_policy_tables_are_read_only_copies() -> None:
    limits = {Sev.HIGH: 0}
    gate = Gate("g", GateScope.ALL_OPEN, limits)
    limits[Sev.HIGH] = 999
    assert gate.max_by_severity[Sev.HIGH] == 0
    with pytest.raises(TypeError):
        gate.max_by_severity[Sev.HIGH] = 5  # type: ignore[index]


def test_requires_utc_now() -> None:
    with pytest.raises(InvalidTimestampError):
        evaluate_quality_gates(POLICY, [], at(0).replace(tzinfo=None))


@pytest.mark.parametrize(
    "build",
    [
        lambda: Gate("Bad Id", GateScope.ALL_OPEN, {Sev.HIGH: 0}),
        lambda: Gate("g", "all_open", {Sev.HIGH: 0}),  # type: ignore[arg-type]
        lambda: Gate("g", GateScope.ALL_OPEN, {}),
        lambda: Gate("g", GateScope.ALL_OPEN, [(Sev.HIGH, 0)]),  # type: ignore[arg-type]
        lambda: Gate("g", GateScope.ALL_OPEN, {"HIGH": 0}),  # type: ignore[dict-item]
        lambda: Gate("g", GateScope.ALL_OPEN, {Sev.HIGH: -1}),
        lambda: QualityGatePolicy(version=2, gates=(), sla_days={}, fail_on_sla_breach=False),
        lambda: QualityGatePolicy(version=1, gates=[], sla_days={}, fail_on_sla_breach=False),  # type: ignore[arg-type]
        lambda: QualityGatePolicy(
            version=1,
            gates=tuple(Gate(f"g{i}", GateScope.ALL_OPEN, {Sev.HIGH: 0}) for i in range(33)),
            sla_days={},
            fail_on_sla_breach=False,
        ),
        lambda: QualityGatePolicy(
            version=1,
            gates=(Gate("g", GateScope.ALL_OPEN, {Sev.HIGH: 0}),) * 2,
            sla_days={},
            fail_on_sla_breach=False,
        ),
        lambda: QualityGatePolicy(
            version=1, gates=(), sla_days={Sev.HIGH: 0}, fail_on_sla_breach=False
        ),
        lambda: QualityGatePolicy(
            version=1, gates=(), sla_days={Sev.HIGH: 3651}, fail_on_sla_breach=False
        ),
        lambda: QualityGatePolicy(version=True, gates=(), sla_days={}, fail_on_sla_breach=False),
        lambda: QualityGatePolicy(version=1, gates=(), sla_days={}, fail_on_sla_breach="no"),  # type: ignore[arg-type]
    ],
)
def test_invalid_policies_are_rejected(build: object) -> None:
    with pytest.raises(InvalidQualityGatePolicyError):
        build()  # type: ignore[operator]


def test_parsers() -> None:
    assert GateScope.parse("new_findings") is GateScope.NEW_FINDINGS
    assert Verdict.parse("FAIL") is Verdict.FAIL


@pytest.mark.parametrize("severity", [Sev.CRITICAL, Sev.HIGH])
def test_default_policy_fails_on_new_critical_or_high(severity: Sev) -> None:
    verdict = evaluate_quality_gates(default_policy(), [snap(severity, new=True)], NOW)
    assert verdict.verdict is Verdict.FAIL


def test_default_policy_passes_old_findings_and_lower_severities() -> None:
    findings = [
        snap(Sev.CRITICAL),
        snap(Sev.HIGH),
        snap(Sev.MEDIUM, new=True),
        snap(Sev.LOW, new=True),
    ]
    assert evaluate_quality_gates(default_policy(), findings, NOW).verdict is Verdict.PASS
