import pytest
from hypothesis import given
from hypothesis import strategies as st

from nave.application.dto.scanning import NormalizedResult
from nave.application.services.fingerprinting import compute_fingerprint, content_hash
from nave.application.services.normalizer import normalize_output, normalize_path
from nave.domain.errors import InvalidSourcePathError
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.rule_id import RuleId
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.source_path import SourcePath
from tests.unit.application.builders import TEST_KEY, output, sast

RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"
SECRET_HASH = "c" * 64


def secret(
    locator: str = SECRET_HASH, snippet: str = "AWS_KEY=AKIAIOSFODNN7EXAMPLE"
) -> NormalizedResult:
    return NormalizedResult(
        rule_id="aws-access-key-id",
        category=FindingCategory.SECRET,
        severity=Severity.CRITICAL,
        title="AWS access key",
        message="AWS access key detected",
        path="config/.env",
        start_line=3,
        end_line=3,
        snippet=snippet,
        locator=locator,
    )


class TestPaths:
    @pytest.mark.parametrize(("raw", "expected"), [("./a/b.py", "a/b.py"), ("a//b/./c", "a/b/c")])
    def test_canonicalizes_harmless_variants(self, raw: str, expected: str) -> None:
        assert normalize_path(raw) == SourcePath(expected)

    @pytest.mark.parametrize(
        "raw", ["/etc/passwd", "../x", "a/../../etc/passwd", "a/..", "", "a\\..\\b"]
    )
    def test_rejects_escapes_instead_of_resolving_them(self, raw: str) -> None:
        with pytest.raises(InvalidSourcePathError):
            normalize_path(raw)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(InvalidSourcePathError):
            normalize_path(None)  # type: ignore[arg-type]


class TestNormalization:
    def test_basic_sast_result(self) -> None:
        result = normalize_output(output(sast()), max_findings=10, secret_key=TEST_KEY)
        (finding,) = result.findings
        assert finding.rule_id == RuleId("python.lang.security.audit.subprocess-shell-true")
        assert finding.location is not None
        assert finding.location.path == SourcePath("app/run.py")
        assert finding.location.start_line == 10
        assert [c.value for c in finding.cwe] == ["CWE-78"]
        assert not result.incomplete

    def test_fingerprint_ignores_line_numbers_and_indentation(self) -> None:
        a = normalize_output(
            output(sast(line=10, snippet="x = run(cmd,  shell=True)")),
            max_findings=10,
            secret_key=TEST_KEY,
        )
        b = normalize_output(
            output(sast(line=99, snippet="    x = run(cmd, shell=True)")),
            max_findings=10,
            secret_key=TEST_KEY,
        )
        assert a.findings[0].fingerprint == b.findings[0].fingerprint

    def test_identical_snippets_in_one_file_get_distinct_stable_fingerprints(self) -> None:
        first, second = sast(line=10), sast(line=20)
        forward = normalize_output(output(first, second), max_findings=10, secret_key=TEST_KEY)
        backward = normalize_output(output(second, first), max_findings=10, secret_key=TEST_KEY)
        assert len(forward.findings) == 2
        assert {f.fingerprint for f in forward.findings} == {
            f.fingerprint for f in backward.findings
        }

    def test_traversal_paths_are_rejected_and_counted(self) -> None:
        result = normalize_output(
            output(sast(path="../../etc/passwd"), sast(path="ok.py")),
            max_findings=10,
            secret_key=TEST_KEY,
        )
        assert len(result.findings) == 1
        assert result.rejected == 1
        assert result.incomplete

    @pytest.mark.parametrize(
        "bad",
        [
            sast(rule="<script>alert(1)</script>"),
            sast(line=0),
            NormalizedResult(
                rule_id="x",
                category="SAST",  # type: ignore[arg-type]
                severity=Severity.LOW,
                title="t",
                message="m",
            ),
            NormalizedResult(
                rule_id="x",
                category=FindingCategory.SAST,
                severity="HIGH",  # type: ignore[arg-type]
                title="t",
                message="m",
            ),
        ],
    )
    def test_invalid_results_are_rejected(self, bad: NormalizedResult) -> None:
        assert normalize_output(output(bad), max_findings=10, secret_key=TEST_KEY).rejected == 1

    def test_untrusted_text_is_revealed_not_rendered(self) -> None:
        result = normalize_output(
            output(sast(snippet="if user" + RLO + " == admin", message="msg\x1b[31m")),
            max_findings=10,
            secret_key=TEST_KEY,
        )
        finding = result.findings[0]
        assert finding.snippet is not None
        assert "<U+202E>" in finding.snippet.value
        assert "<U+001B>" in finding.message.value

    def test_secret_snippet_is_never_kept(self) -> None:
        finding = normalize_output(output(secret()), max_findings=10, secret_key=TEST_KEY).findings[
            0
        ]
        assert finding.snippet is None
        assert "AKIA" not in finding.message.value

    @pytest.mark.parametrize("locator", ["AKIAIOSFODNN7EXAMPLE", "", "C" * 64])
    def test_secret_locator_must_be_a_hash(self, locator: str) -> None:
        assert (
            normalize_output(
                output(secret(locator=locator)), max_findings=10, secret_key=TEST_KEY
            ).rejected
            == 1
        )

    def test_dependency_locator_is_required_and_bounded(self) -> None:
        dep = NormalizedResult(
            rule_id="CVE-2024-3094",
            category=FindingCategory.DEPENDENCY,
            severity=Severity.CRITICAL,
            title="xz backdoor",
            message="xz-utils 5.6.0",
            path="Dockerfile",
            locator="deb:xz-utils",
            cve=("CVE-2024-3094", "not-a-cve"),
        )
        ok = normalize_output(output(dep), max_findings=10, secret_key=TEST_KEY)
        assert [c.value for c in ok.findings[0].cve] == ["CVE-2024-3094"]
        assert ok.findings[0].location is not None
        assert ok.findings[0].location.start_line is None
        bad = NormalizedResult(
            **{**{k: getattr(dep, k) for k in dep.__slots__}, "locator": "has space"}
        )
        assert normalize_output(output(bad), max_findings=10, secret_key=TEST_KEY).rejected == 1

    def test_duplicates_merge_keeping_highest_severity(self) -> None:
        low = secret()
        high = NormalizedResult(
            **{**{k: getattr(low, k) for k in low.__slots__}, "severity": Severity.LOW}
        )
        result = normalize_output(output(high, low), max_findings=10, secret_key=TEST_KEY)
        assert len(result.findings) == 1
        assert result.findings[0].severity is Severity.CRITICAL

    def test_truncation_keeps_most_severe(self) -> None:
        results = [sast(rule=f"r{i}", severity=Severity.LOW) for i in range(5)]
        results.append(sast(rule="critical-one", severity=Severity.CRITICAL))
        result = normalize_output(output(*results), max_findings=2, secret_key=TEST_KEY)
        assert result.truncated
        assert result.findings[0].severity is Severity.CRITICAL
        assert len(result.findings) == 2

    def test_scanner_reported_truncation_propagates(self) -> None:
        assert normalize_output(
            output(sast(), truncated=True), max_findings=10, secret_key=TEST_KEY
        ).incomplete

    def test_result_without_path_has_no_location(self) -> None:
        assert (
            normalize_output(
                output(sast(path=None, line=None)), max_findings=10, secret_key=TEST_KEY
            )
            .findings[0]
            .location
            is None
        )

    def test_references_are_capped(self) -> None:
        many = NormalizedResult(
            **{
                **{k: getattr(sast(), k) for k in sast().__slots__},
                "cwe": (*(f"CWE-{i}" for i in range(1, 100)), "CWE-1"),
            }
        )
        assert (
            len(
                normalize_output(output(many), max_findings=10, secret_key=TEST_KEY).findings[0].cwe
            )
            == 32
        )

    def test_requires_positive_limit(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            normalize_output(output(), max_findings=0, secret_key=TEST_KEY)

    @given(
        st.lists(
            st.builds(
                NormalizedResult,
                rule_id=st.text(max_size=40),
                category=st.sampled_from(FindingCategory),
                severity=st.sampled_from(Severity),
                title=st.text(max_size=50),
                message=st.text(max_size=50),
                path=st.none() | st.text(max_size=40),
                start_line=st.none() | st.integers(-5, 50),
                end_line=st.none() | st.integers(-5, 50),
                snippet=st.none() | st.text(max_size=50),
                locator=st.text(max_size=70),
            ),
            max_size=20,
        )
    )
    def test_arbitrary_scanner_output_never_crashes(self, results: list[NormalizedResult]) -> None:
        result = normalize_output(output(*results), max_findings=10, secret_key=TEST_KEY)
        assert len(result.findings) + result.rejected <= len(results)
        for finding in result.findings:
            if finding.location is not None:
                assert ".." not in finding.location.path.value.split("/")


class TestFingerprint:
    def test_length_prefix_prevents_concatenation_collisions(self) -> None:
        kind = ScannerKind("semgrep")
        a = compute_fingerprint(
            scanner_kind=kind,
            category=FindingCategory.SAST,
            rule_id=RuleId("a.b"),
            path=SourcePath("c"),
            locator="",
        )
        b = compute_fingerprint(
            scanner_kind=kind,
            category=FindingCategory.SAST,
            rule_id=RuleId("a"),
            path=SourcePath("b.c"),
            locator="",
        )
        assert a != b

    def test_content_hash_normalizes_whitespace(self) -> None:
        assert content_hash("a  b\n\tc") == content_hash("a b c")


def test_lower_severity_duplicate_does_not_replace() -> None:
    critical = secret()
    low = NormalizedResult(
        **{
            **{k: getattr(critical, k) for k in critical.__slots__},
            "severity": Severity.LOW,
            "start_line": 9,
            "end_line": 9,
        }
    )
    result = normalize_output(output(critical, low), max_findings=10, secret_key=TEST_KEY)
    assert result.findings[0].severity is Severity.CRITICAL


def test_duplicate_references_are_collapsed() -> None:
    base = sast()
    dup = NormalizedResult(
        **{**{k: getattr(base, k) for k in base.__slots__}, "cwe": ("CWE-78", "CWE-78", "CWE-79")}
    )
    assert [
        c.value
        for c in normalize_output(output(dup), max_findings=10, secret_key=TEST_KEY).findings[0].cwe
    ] == [
        "CWE-78",
        "CWE-79",
    ]
