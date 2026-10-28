import pytest
from hypothesis import given
from hypothesis import strategies as st

from nave.domain.errors import InvalidStatusReasonError
from nave.domain.value_objects.status_reason import MAX_DETAIL_LENGTH, ReasonCode, StatusReason
from nave.domain.value_objects.text import is_safe_text, sanitize_untrusted


def test_accepts_code_without_detail() -> None:
    reason = StatusReason(ReasonCode.TIMEOUT)
    assert reason.detail == ""


@pytest.mark.parametrize(
    "detail",
    [
        "a\nb",
        "\x1b[31mred",
        "x\N{RIGHT-TO-LEFT OVERRIDE}evil",
        "x\N{ZERO WIDTH SPACE}",
        "a" * (MAX_DETAIL_LENGTH + 1),
    ],
)
def test_rejects_unsafe_or_long_detail(detail: str) -> None:
    with pytest.raises(InvalidStatusReasonError):
        StatusReason(ReasonCode.SCANNER_FAILED, detail)


def test_rejects_wrong_types() -> None:
    with pytest.raises(InvalidStatusReasonError):
        StatusReason("TIMEOUT")  # type: ignore[arg-type]
    with pytest.raises(InvalidStatusReasonError):
        StatusReason(ReasonCode.TIMEOUT, None)  # type: ignore[arg-type]


def test_from_untrusted_replaces_control_and_format_characters() -> None:
    reason = StatusReason.from_untrusted(
        ReasonCode.SCANNER_FAILED, "panic:\x1b[31m boom\nnext\N{RIGHT-TO-LEFT OVERRIDE}line"
    )
    assert reason.detail == "panic:?[31m boom?next?line"


def test_from_untrusted_truncates_with_visible_ellipsis() -> None:
    reason = StatusReason.from_untrusted(ReasonCode.SCANNER_FAILED, "x" * 10_000)
    assert len(reason.detail) == MAX_DETAIL_LENGTH
    assert reason.detail.endswith("\N{HORIZONTAL ELLIPSIS}")


@given(st.text(max_size=2_000))
def test_from_untrusted_always_produces_a_valid_reason(text: str) -> None:
    reason = StatusReason.from_untrusted(ReasonCode.INTERNAL_ERROR, text)
    assert is_safe_text(reason.detail)
    assert len(reason.detail) <= MAX_DETAIL_LENGTH


def test_sanitize_requires_positive_length() -> None:
    with pytest.raises(ValueError, match="positive"):
        sanitize_untrusted("x", 0)


def test_from_untrusted_handles_huge_input() -> None:
    reason = StatusReason.from_untrusted(ReasonCode.SCANNER_FAILED, "\x00" * 10_000_000)
    assert reason.detail == "?" * (MAX_DETAIL_LENGTH - 1) + "\N{HORIZONTAL ELLIPSIS}"


def test_from_untrusted_keeps_text_at_exact_limit() -> None:
    reason = StatusReason.from_untrusted(ReasonCode.SCANNER_FAILED, "y" * MAX_DETAIL_LENGTH)
    assert reason.detail == "y" * MAX_DETAIL_LENGTH
