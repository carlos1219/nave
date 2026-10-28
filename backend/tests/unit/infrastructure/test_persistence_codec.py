"""El JSON leído de la base de datos se trata como no confiable (fila corrupta o
manipulada): cada decodificador lo rechaza con un error de dominio, nunca a medias."""

from datetime import UTC, datetime

import pytest

from nave.domain.errors import InvalidValueError
from nave.domain.quality_gates import Gate, GateScope, PolicyVerdict, QualityGatePolicy, Verdict
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.infrastructure.persistence.codec import (
    MAX_REASONS,
    _encode_value,
    decode_policy,
    decode_summary,
    decode_verdict,
    encode_policy,
    encode_summary,
)

SUMMARY = ScanSummary(
    new=SeverityCounts(high=1), open=SeverityCounts(low=2), verdict=Verdict.PASS, truncated=True
)
GOOD_REASON = {
    "kind": "GATE",
    "rule": "g",
    "blocking": True,
    "severity": "HIGH",
    "observed": 1,
    "limit": 0,
}


def test_summary_round_trip() -> None:
    assert decode_summary(encode_summary(SUMMARY)) == SUMMARY


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {1: "x"},
        {**encode_summary(SUMMARY), "extra": 1},
        {**encode_summary(SUMMARY), "new": {"high": "1"}},
        {**encode_summary(SUMMARY), "new": {"high": True}},
        {**encode_summary(SUMMARY), "new": {"bogus": 1}},
        {**encode_summary(SUMMARY), "verdict": 3},
        {**encode_summary(SUMMARY), "verdict": "pass"},
        {**encode_summary(SUMMARY), "truncated": "yes"},
    ],
)
def test_corrupt_summary_is_rejected(raw: object) -> None:
    with pytest.raises(InvalidValueError):
        decode_summary(raw)


def test_verdict_round_trip_without_severity() -> None:
    verdict = decode_verdict("NEUTRAL", [{**GOOD_REASON, "kind": "TRUNCATED", "severity": None}])
    assert verdict.reasons[0].severity is None


@pytest.mark.parametrize(
    "reasons",
    [
        {"not": "a list"},
        [GOOD_REASON] * (MAX_REASONS + 1),
        ["text"],
        [{**GOOD_REASON, "extra": 1}],
        [{**GOOD_REASON, "kind": "EVIL"}],
        [{**GOOD_REASON, "observed": 1.5}],
        [{**GOOD_REASON, "rule": None}],
    ],
)
def test_corrupt_reasons_are_rejected(reasons: object) -> None:
    with pytest.raises(InvalidValueError):
        decode_verdict("FAIL", reasons)


def test_policy_round_trip_and_defaults() -> None:
    policy = QualityGatePolicy(
        version=1,
        gates=(Gate("g", GateScope.ALL_OPEN, {Severity.HIGH: 0}),),
        sla_days={Severity.HIGH: 30},
        fail_on_sla_breach=True,
    )
    assert decode_policy(encode_policy(policy)) == policy
    minimal = decode_policy({"version": 1, "gates": []})
    assert minimal.sla_days == {}
    assert minimal.fail_on_sla_breach is False


@pytest.mark.parametrize(
    "raw",
    [
        {"version": 1, "gates": {}},
        {"version": 1, "gates": [], "unknown": True},
        {"version": 1, "gates": [{"id": "g", "scope": "all_open", "max": {"HIGH": 0}, "x": 1}]},
        {"version": 1, "gates": [{"id": "g", "scope": "all_open", "max": []}]},
        {"version": 1, "gates": [], "sla_days": {"high": 3}},
        {"version": "1", "gates": []},
    ],
)
def test_corrupt_policy_is_rejected(raw: object) -> None:
    with pytest.raises(InvalidValueError):
        decode_policy(raw)


def test_outbox_rejects_unknown_value_types() -> None:
    assert _encode_value(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00+00:00"
    with pytest.raises(TypeError, match="cannot encode"):
        _encode_value(PolicyVerdict(Verdict.PASS))
