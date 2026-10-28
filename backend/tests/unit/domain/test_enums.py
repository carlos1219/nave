import pytest

from nave.domain.errors import UnknownEnumValueError
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.status_reason import ReasonCode
from nave.domain.value_objects.triage_state import TriageState
from nave.domain.value_objects.trigger import ScanTrigger


def test_severity_order_is_by_impact_not_alphabetical() -> None:
    assert Severity.CRITICAL > Severity.HIGH > Severity.MEDIUM > Severity.LOW > Severity.INFO
    assert Severity.HIGH >= Severity.HIGH
    assert Severity.LOW <= Severity.HIGH
    shuffled = [Severity.LOW, Severity.CRITICAL, Severity.INFO, Severity.HIGH, Severity.MEDIUM]
    assert sorted(shuffled, reverse=True) == list(Severity)


def test_severity_does_not_compare_with_other_types() -> None:
    with pytest.raises(TypeError):
        _ = Severity.HIGH < "LOW"


@pytest.mark.parametrize(
    ("required", "granted", "expected"),
    [
        (Role.VIEWER, Role.VIEWER, True),
        (Role.VIEWER, Role.ADMIN, True),
        (Role.TRIAGER, Role.VIEWER, False),
        (Role.TRIAGER, Role.TRIAGER, True),
        (Role.ADMIN, Role.TRIAGER, False),
        (Role.ADMIN, Role.ADMIN, True),
    ],
)
def test_role_hierarchy(required: Role, granted: Role, expected: bool) -> None:
    assert granted.satisfies(required) is expected


@pytest.mark.parametrize(
    "enum_type", [Severity, ScanStatus, TriageState, Role, ScanTrigger, ReasonCode]
)
def test_parse_accepts_every_exact_value(enum_type: type[Severity]) -> None:
    for member in enum_type:
        assert enum_type.parse(member.value) is member


@pytest.mark.parametrize(
    "raw", ["high", " HIGH", "HIGH ", "HIGH\n", "", "H\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}GH"]
)
def test_parse_is_strict(raw: str) -> None:
    with pytest.raises(UnknownEnumValueError):
        Severity.parse(raw)


def test_parse_rejects_non_string() -> None:
    with pytest.raises(UnknownEnumValueError):
        Severity.parse(3)  # type: ignore[arg-type]


def test_error_message_does_not_echo_input() -> None:
    payload = "<script>alert(1)</script>"
    with pytest.raises(UnknownEnumValueError) as excinfo:
        Severity.parse(payload)
    assert payload not in str(excinfo.value)
