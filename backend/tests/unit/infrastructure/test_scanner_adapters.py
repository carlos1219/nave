import base64
import hashlib
from dataclasses import replace

import pytest

from nave.application.dto.scanning import ScannerOutput
from nave.application.dto.workspace import WorkspaceHandle
from nave.application.errors import ScannerExecutionError, ScannerOutputInvalidError
from nave.application.services.normalizer import normalize_output
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.severity import Severity
from nave.infrastructure.scanners.registry import ScannerRegistry
from nave.infrastructure.scanners.runner_scanner import RunnerScanner
from nave.infrastructure.scanners.semgrep import semgrep_scanner
from nave.infrastructure.scanners.trivy import trivy_scanner
from tests.fakes import sarif as s
from tests.fakes.runner import DIGEST, RUN, client, ok, scan_reply
from tests.unit.application.builders import SCAN_LIMITS, TEST_KEY

HANDLE = WorkspaceHandle(RUN)
REPO_FILE = "repo/src/app.py"


def reply(data: bytes, **overrides: object) -> object:
    return ok(scan_reply(stdout_b64=base64.b64encode(data).decode(), **overrides))


def run(factory: object, data: bytes, **overrides: object) -> ScannerOutput:
    runner, _ = client(reply(data, **overrides))  # type: ignore[arg-type]
    scanner: RunnerScanner = factory(runner, expected_version="1.2.3")  # type: ignore[operator]
    return scanner.scan(HANDLE, SCAN_LIMITS)


class TestSemgrep:
    def test_maps_findings_and_metadata(self) -> None:
        output = run(semgrep_scanner, s.semgrep_sample())
        assert output.kind.value == "semgrep"
        assert output.image_digest == DIGEST
        assert output.output_sha256 == hashlib.sha256(s.semgrep_sample()).hexdigest()
        assert not output.truncated
        high, low = output.results
        assert (high.severity, high.category) == (Severity.HIGH, FindingCategory.SAST)
        assert high.cwe == ("CWE-78",)
        assert high.snippet == "subprocess.run(cmd, shell=True)"
        assert low.severity is Severity.LOW

    def test_unknown_level_is_not_understated(self) -> None:
        data = s.document([], [s.result("r", level="weird", uri=REPO_FILE)], name="Semgrep OSS")
        assert run(semgrep_scanner, data).results[0].severity is Severity.MEDIUM

    def test_rule_default_level_is_used_when_the_result_has_none(self) -> None:
        data = s.document([s.rule("r", level="error")], [s.result("r", level=None, uri=REPO_FILE)])
        assert run(semgrep_scanner, data).results[0].severity is Severity.HIGH

    def test_duplicate_cwe_tags_are_collapsed(self) -> None:
        data = s.document(
            [s.rule("r", "CWE-79: a", "CWE-79: b", "CWE-89: c")], [s.result("r", uri=REPO_FILE)]
        )
        assert run(semgrep_scanner, data).results[0].cwe == ("CWE-79", "CWE-89")

    @pytest.mark.parametrize(
        ("rule_id", "tags"),
        [
            ("python.lang.hardcoded-password", ()),
            ("generic.api_key.exposed", ()),
            ("custom.rule-1", ("CWE-798: Use of Hard-coded Credentials",)),
            ("custom.rule-2", ("CWE-522: Insufficiently Protected Credentials",)),
        ],
    )
    def test_sensitive_sast_rules_never_carry_the_matched_text(
        self, rule_id: str, tags: tuple[str, ...]
    ) -> None:
        leaked = "AKIAIOSFODNN7EXAMPLE"
        data = s.document(
            [s.rule(rule_id, *tags)],
            [s.result(rule_id, message=f"found {leaked}", uri=REPO_FILE, snippet=f"k='{leaked}'")],
        )
        found = run(semgrep_scanner, data).results[0]
        assert found.snippet is None
        assert leaked not in found.message

    def test_non_sensitive_rules_keep_their_snippet(self) -> None:
        data = s.document(
            [s.rule("r", "CWE-78: x")], [s.result("r", uri=REPO_FILE, snippet="run(cmd)")]
        )
        assert run(semgrep_scanner, data).results[0].snippet == "run(cmd)"


class TestTrivy:
    def test_maps_the_three_categories(self) -> None:
        output = run(trivy_scanner, s.trivy_sample())
        vuln, secret, iac = output.results
        assert (vuln.category, vuln.severity) == (FindingCategory.DEPENDENCY, Severity.HIGH)
        assert vuln.cve == ("CVE-2021-23337",)
        assert vuln.locator == "pkg:lodash"
        assert (secret.category, secret.severity) == (FindingCategory.SECRET, Severity.CRITICAL)
        assert (iac.category, iac.severity) == (FindingCategory.IAC, Severity.HIGH)
        assert iac.locator.startswith("msg:")

    def test_secret_locator_is_a_hash_of_the_censored_text_and_snippet_is_absent(self) -> None:
        secret = run(trivy_scanner, s.trivy_sample()).results[1]
        assert secret.locator == hashlib.sha256(s.TRIVY_SECRET.encode()).hexdigest()
        assert secret.snippet is None

    def test_everything_survives_the_normalizer(self) -> None:
        output = run(trivy_scanner, s.trivy_sample())
        result = normalize_output(output, max_findings=10, secret_key=TEST_KEY)
        assert len(result.findings) == 3
        assert not result.incomplete

    def test_severity_falls_back_to_the_score_and_then_to_the_level(self) -> None:
        cases = [
            (s.rule("r", "vulnerability", score="9.8"), Severity.CRITICAL),
            (s.rule("r", "vulnerability", score="7.0"), Severity.HIGH),
            (s.rule("r", "vulnerability", score="4.0"), Severity.MEDIUM),
            (s.rule("r", "vulnerability", score="0.1"), Severity.LOW),
            (s.rule("r", "vulnerability", score="0"), Severity.INFO),
            (s.rule("r", "vulnerability"), Severity.HIGH),
            (s.rule("r", "vulnerability", "UNKNOWN"), Severity.INFO),
        ]
        for rule, expected in cases:
            data = s.document([rule], [s.result("r", "Package: p")], name="Trivy")
            assert run(trivy_scanner, data).results[0].severity is expected, rule

    def test_results_of_unknown_kind_are_dropped_and_make_the_output_incomplete(self) -> None:
        data = s.document(
            [s.rule("lic", "license"), s.rule("CVE-2024-1", "vulnerability", "LOW")],
            [s.result("lic"), s.result("CVE-2024-1", "Package: x"), s.result("no-rule")],
        )
        output = run(trivy_scanner, data)
        assert len(output.results) == 1
        assert output.truncated

    def test_hostile_package_names_cannot_break_the_locator(self) -> None:
        data = s.document(
            [s.rule("r", "vulnerability", "LOW")],
            [s.result("r", "Package: a<script>b\n")],
        )
        locator = run(trivy_scanner, data).results[0].locator
        assert locator == "pkg:a_script_b"

    def test_a_dependency_without_a_package_line_still_has_a_locator(self) -> None:
        data = s.document([s.rule("r", "vulnerability", "LOW")], [s.result("r", "no package")])
        assert run(trivy_scanner, data).results[0].locator == "pkg:unknown"

    def test_a_cve_id_that_is_not_a_cve_is_not_reported_as_one(self) -> None:
        data = s.document([s.rule("GHSA-xxxx", "vulnerability", "LOW")], [s.result("GHSA-xxxx")])
        assert run(trivy_scanner, data).results[0].cve == ()


class TestSharedBehaviour:
    def test_version_mismatch_is_refused(self) -> None:
        with pytest.raises(ScannerExecutionError):
            run(semgrep_scanner, s.semgrep_sample(), scanner_version="9.9.9")

    def test_nonzero_exit_code_is_refused(self) -> None:
        with pytest.raises(ScannerExecutionError):
            run(semgrep_scanner, s.semgrep_sample(), exit_code=2)

    @pytest.mark.parametrize("digest", ["latest", "sha256:abc", ""])
    def test_bad_image_digest_is_an_invalid_output(self, digest: str) -> None:
        with pytest.raises(ScannerOutputInvalidError):
            run(semgrep_scanner, s.semgrep_sample(), image_digest=digest)

    def test_garbage_output_is_an_invalid_output(self) -> None:
        with pytest.raises(ScannerOutputInvalidError):
            run(trivy_scanner, b"<html>")

    def test_max_results_truncates_and_flags_the_output(self) -> None:
        many = s.document([], [s.result(f"r{i}", uri=REPO_FILE) for i in range(5)])
        runner, _ = client(reply(many))  # type: ignore[arg-type]
        limits = replace(SCAN_LIMITS, max_results=3)
        output = semgrep_scanner(runner, expected_version="1.2.3").scan(HANDLE, limits)
        assert len(output.results) == 3
        assert output.truncated

    def test_the_workspace_handle_is_the_run_id(self) -> None:
        runner, transport = client(reply(s.semgrep_sample()))  # type: ignore[arg-type]
        semgrep_scanner(runner, expected_version="1.2.3").scan(HANDLE, SCAN_LIMITS)
        assert f'"run_id":"{RUN}"'.encode() in transport.calls[0][1]

    def test_traversal_paths_from_a_hostile_scanner_never_become_findings(self) -> None:
        data = s.document(
            [],
            [
                s.result("a", uri="../../etc/passwd"),
                s.result("b", uri="/etc/passwd"),
                s.result("c", uri="repo/../../etc/passwd"),
            ],
        )
        output = run(semgrep_scanner, data)
        normalized = normalize_output(output, max_findings=10, secret_key=TEST_KEY)
        assert normalized.findings == ()
        assert normalized.incomplete

    def test_semgrep_drops_paths_outside_the_repository(self) -> None:
        data = s.document(
            [],
            [
                s.result("a", uri="../../etc/passwd"),
                s.result("b", uri="/etc/passwd"),
                s.result("c", uri="other/app.py"),
                s.result("d", uri="repo/ok.py"),
            ],
        )
        output = run(semgrep_scanner, data)
        assert [r.rule_id for r in output.results] == ["d"]
        assert output.results[0].path == "ok.py"
        assert output.truncated

    def test_semgrep_traversal_that_keeps_the_prefix_is_rejected_by_the_normalizer(self) -> None:
        data = s.document([], [s.result("a", uri="repo/../../etc/passwd")])
        normalized = normalize_output(
            run(semgrep_scanner, data), max_findings=10, secret_key=TEST_KEY
        )
        assert normalized.findings == ()
        assert normalized.rejected == 1

    def test_files_skipped_for_size_become_findings_and_make_the_output_incomplete(self) -> None:
        # F7-02: Semgrep omite sin avisar los ficheros grandes; el envoltorio los declara.
        data = s.document(
            [],
            [
                s.result("a", uri=REPO_FILE),
                s.result(
                    "nave.scan.oversized-file-skipped",
                    "The file is larger than 2000000 bytes and was not analyzed by Semgrep.",
                    uri="repo/big/padded.py",
                    start=None,
                    end=None,
                    level="warning",
                ),
            ],
            name="Semgrep OSS",
        )
        output = run(semgrep_scanner, data)
        assert output.truncated
        skipped = next(r for r in output.results if r.rule_id.startswith("nave.scan.oversized-"))
        assert (skipped.path, skipped.severity) == ("big/padded.py", Severity.MEDIUM)
        normalized = normalize_output(output, max_findings=10, secret_key=TEST_KEY)
        assert normalized.incomplete
        assert {f.rule_id.value for f in normalized.findings} == {
            "a",
            "nave.scan.oversized-file-skipped",
        }

    def test_the_overflow_notice_has_no_location_and_still_marks_the_output(self) -> None:
        data = s.document(
            [],
            [
                s.result(
                    "nave.scan.oversized-files-more",
                    "More than 100 files larger than 2000000 bytes were not analyzed.",
                    uri=None,
                    level="warning",
                )
            ],
        )
        output = run(semgrep_scanner, data)
        assert output.truncated
        assert output.results[0].path is None

    def test_a_clean_run_is_not_flagged_as_skipping_code(self) -> None:
        assert not run(semgrep_scanner, s.semgrep_sample()).truncated


class TestRegistry:
    def test_keeps_order(self) -> None:
        runner, _ = client()
        a, b = (
            semgrep_scanner(runner, expected_version="1"),
            trivy_scanner(runner, expected_version="1"),
        )
        assert [x.kind.value for x in ScannerRegistry([a, b]).scanners()] == ["semgrep", "trivy"]

    def test_rejects_empty_and_duplicates(self) -> None:
        runner, _ = client()
        a = semgrep_scanner(runner, expected_version="1")
        with pytest.raises(ValueError, match="at least one"):
            ScannerRegistry([])
        with pytest.raises(ValueError, match="unique"):
            ScannerRegistry([a, a])
