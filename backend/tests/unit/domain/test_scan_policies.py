import itertools

import pytest

from nave.domain.errors import InvalidScanTransitionError
from nave.domain.policies import (
    SCAN_TRANSITIONS,
    allowed_reasons,
    can_transition,
    ensure_scan_transition,
)
from nave.domain.value_objects.scan_status import ScanStatus as S
from nave.domain.value_objects.status_reason import ReasonCode

EXPECTED = {
    (S.QUEUED, S.FETCHING),
    (S.QUEUED, S.CANCELLED),
    (S.QUEUED, S.TIMED_OUT),
    (S.FETCHING, S.SCANNING),
    (S.FETCHING, S.FAILED),
    (S.FETCHING, S.CANCELLED),
    (S.FETCHING, S.TIMED_OUT),
    (S.SCANNING, S.PROCESSING),
    (S.SCANNING, S.FAILED),
    (S.SCANNING, S.CANCELLED),
    (S.SCANNING, S.TIMED_OUT),
    (S.PROCESSING, S.COMPLETED),
    (S.PROCESSING, S.FAILED),
}


@pytest.mark.parametrize(("current", "target"), list(itertools.product(S, S)))
def test_every_pair_matches_the_documented_state_machine(current: S, target: S) -> None:
    expected = (current, target) in EXPECTED
    assert can_transition(current, target) is expected
    if expected:
        ensure_scan_transition(current, target)
    else:
        with pytest.raises(InvalidScanTransitionError) as excinfo:
            ensure_scan_transition(current, target)
        assert (excinfo.value.current, excinfo.value.target) == (current, target)


def test_table_covers_every_status() -> None:
    assert set(SCAN_TRANSITIONS) == set(S)


@pytest.mark.parametrize("status", list(S))
def test_terminal_states_are_exactly_those_without_outgoing_transitions(status: S) -> None:
    assert status.is_terminal is (not SCAN_TRANSITIONS[status])


def test_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        SCAN_TRANSITIONS[S.COMPLETED] = frozenset({S.QUEUED})  # type: ignore[index]


def test_reasons_only_for_unsuccessful_terminal_states() -> None:
    for status in S:
        has_reasons = bool(allowed_reasons(status))
        assert has_reasons is (status in {S.FAILED, S.TIMED_OUT, S.CANCELLED})


def test_every_reason_code_belongs_to_exactly_one_status() -> None:
    owners = {code: [s for s in S if code in allowed_reasons(s)] for code in ReasonCode}
    assert all(len(statuses) == 1 for statuses in owners.values())
