import pytest
from hypothesis import given
from hypothesis import strategies as st

from nave.domain.errors import (
    FindingInvariantError,
    InvalidFindingTextError,
    InvalidReferenceError,
    InvalidRuleIdError,
    InvalidSourcePathError,
    InvalidValueError,
)
from nave.domain.quality_gates import Verdict
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import (
    FindingMessage,
    FindingTitle,
    Justification,
    Snippet,
)
from nave.domain.value_objects.location import MAX_LINE, SourceLocation
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.rule_id import CveId, CweId, RuleId
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.source_path import SourcePath
from nave.domain.value_objects.text import is_safe_text, reveal_unsafe
from tests.unit.domain.builders import FINDING_ID, SCAN_ID

RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"


class TestSourcePath:
    @pytest.mark.parametrize(
        "raw",
        [
            "a.py",
            "src/app/main.py",
            ".github/workflows/ci.yml",
            "dir with space/f.py",
            "x/\N{LATIN SMALL LETTER N WITH TILDE}.go",
        ],
    )
    def test_accepts_canonical_relative_paths(self, raw: str) -> None:
        assert str(SourcePath(raw)) == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "/etc/passwd",
            "../secret",
            "a/../../b",
            "./a.py",
            "a/./b",
            "a//b",
            "a/",
            "C:/Windows/x",
            "c:x",
            "a\\b",
            "a\x00b",
            "a\nb",
            "a" + RLO + "b",
            "a" * 1025,
        ],
    )
    def test_rejects_non_canonical_or_escaping_paths(self, raw: str) -> None:
        with pytest.raises(InvalidSourcePathError):
            SourcePath(raw)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(InvalidSourcePathError):
            SourcePath(None)  # type: ignore[arg-type]

    @given(st.text(max_size=300))
    def test_accepted_paths_never_escape(self, raw: str) -> None:
        try:
            path = SourcePath(raw)
        except InvalidSourcePathError:
            return
        parts = path.value.split("/")
        assert ".." not in parts
        assert not path.value.startswith("/")


class TestSourceLocation:
    def test_valid_range(self) -> None:
        loc = SourceLocation(SourcePath("a.py"), 3, 5)
        assert (loc.start_line, loc.end_line) == (3, 5)

    def test_lines_are_optional_together(self) -> None:
        assert SourceLocation(SourcePath("go.mod")).start_line is None

    @pytest.mark.parametrize(
        ("start", "end"), [(0, 1), (5, 4), (1, MAX_LINE + 1), (True, 2), (None, 3), (3, None)]
    )
    def test_rejects_invalid_ranges(self, start: int | None, end: int | None) -> None:
        with pytest.raises(InvalidSourcePathError):
            SourceLocation(SourcePath("a.py"), start, end)

    def test_rejects_raw_string_path(self) -> None:
        with pytest.raises(InvalidSourcePathError):
            SourceLocation("a.py", 1, 1)  # type: ignore[arg-type]


class TestIdentifiersOfRules:
    @pytest.mark.parametrize(
        "raw", ["python.lang.security.audit.x", "GHSA-xxxx-yyyy-zzzz", "AVD-AWS-0001", "a"]
    )
    def test_rule_ids(self, raw: str) -> None:
        assert str(RuleId(raw)) == raw

    @pytest.mark.parametrize("raw", ["", "-x", "a b", "a\nb", "a" * 257, "<script>"])
    def test_invalid_rule_ids(self, raw: str) -> None:
        with pytest.raises(InvalidRuleIdError):
            RuleId(raw)

    def test_cwe_and_cve(self) -> None:
        assert str(CweId("CWE-79")) == "CWE-79"
        assert str(CveId("CVE-2024-3094")) == "CVE-2024-3094"

    @pytest.mark.parametrize("raw", ["CWE-0", "cwe-79", "CWE-79\n", "CWE-1234567"])
    def test_invalid_cwe(self, raw: str) -> None:
        with pytest.raises(InvalidReferenceError):
            CweId(raw)

    @pytest.mark.parametrize("raw", ["CVE-24-1", "CVE-2024-123", "cve-2024-3094", "CVE-1899-1234"])
    def test_invalid_cve(self, raw: str) -> None:
        with pytest.raises(InvalidReferenceError):
            CveId(raw)


class TestFindingText:
    def test_title_is_single_line(self) -> None:
        with pytest.raises(InvalidFindingTextError):
            FindingTitle("line1\nline2")

    def test_snippet_allows_newlines_and_tabs(self) -> None:
        assert Snippet("def f():\n\treturn 1").value.count("\n") == 1

    @pytest.mark.parametrize("raw", ["", "x" * 4097, "a\x1bb", "a" + RLO + "b"])
    def test_message_rejects_invalid(self, raw: str) -> None:
        with pytest.raises(InvalidFindingTextError):
            FindingMessage(raw)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(InvalidFindingTextError):
            FindingTitle(5)  # type: ignore[arg-type]

    def test_from_untrusted_reveals_trojan_source(self) -> None:
        snippet = Snippet.from_untrusted("access = 'user" + RLO + " // admin'\n")
        assert "<U+202E>" in snippet.value
        assert RLO not in snippet.value

    def test_from_untrusted_handles_empty_and_huge(self) -> None:
        assert FindingTitle.from_untrusted("").value == "-"
        huge = FindingMessage.from_untrusted("\x00" * 1_000_000)
        assert len(huge.value) == FindingMessage.max_length
        assert huge.value.endswith("\N{HORIZONTAL ELLIPSIS}")

    def test_justification_requires_ten_characters(self) -> None:
        with pytest.raises(InvalidFindingTextError):
            Justification("ok")
        assert Justification("Accepted until the vendor ships a fix.").value

    @given(st.text(max_size=5000))
    def test_from_untrusted_always_valid(self, raw: str) -> None:
        message = FindingMessage.from_untrusted(raw)
        assert is_safe_text(message.value, frozenset("\n\t"))
        assert 1 <= len(message.value) <= FindingMessage.max_length

    def test_reveal_requires_positive_length(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            reveal_unsafe("x", 0)

    def test_reveal_truncates_when_expansion_overflows(self) -> None:
        assert reveal_unsafe(RLO * 3, 3) == "<U\N{HORIZONTAL ELLIPSIS}"


class TestCounts:
    def test_of_counts_by_severity(self) -> None:
        counts = SeverityCounts.of([Severity.HIGH, Severity.HIGH, Severity.LOW])
        assert (counts.high, counts.low, counts.total) == (2, 1, 3)
        assert counts.get(Severity.CRITICAL) == 0

    @pytest.mark.parametrize("value", [-1, True, 2**31])
    def test_rejects_invalid_counts(self, value: int) -> None:
        with pytest.raises(InvalidValueError):
            SeverityCounts(high=value)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"new": {}, "open": SeverityCounts(), "verdict": Verdict.PASS, "truncated": False},
            {"new": SeverityCounts(), "open": None, "verdict": Verdict.PASS, "truncated": False},
            {
                "new": SeverityCounts(),
                "open": SeverityCounts(),
                "verdict": "PASS",
                "truncated": False,
            },
            {
                "new": SeverityCounts(),
                "open": SeverityCounts(),
                "verdict": Verdict.PASS,
                "truncated": 0,
            },
        ],
    )
    def test_summary_rejects_invalid_fields(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(InvalidValueError):
            ScanSummary(**kwargs)  # type: ignore[arg-type]


def test_text_str_and_category_parse() -> None:
    assert str(FindingTitle("x")) == "x"
    assert FindingCategory.parse("SECRET") is FindingCategory.SECRET


def test_occurrence_requires_boolean_is_new() -> None:
    with pytest.raises(FindingInvariantError):
        FindingOccurrence(
            scan_id=SCAN_ID,
            finding_id=FINDING_ID,
            location=None,
            message=FindingMessage("m"),
            snippet=None,
            is_new="yes",  # type: ignore[arg-type]
        )
