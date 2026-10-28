import json

import pytest

from nave.application.errors import ScannerOutputInvalidError
from nave.infrastructure.scanners.sarif import MAX_RULES, parse_sarif
from tests.fakes import sarif as s


def test_parses_results_with_rule_metadata() -> None:
    parsed = parse_sarif(s.semgrep_sample())
    assert parsed.tool_name == "Semgrep OSS"
    assert parsed.dropped == 0
    first = parsed.results[0]
    assert first.rule is not None
    assert first.rule.level == "error"
    assert first.rule.tags[0].startswith("CWE-78")
    assert (first.path, first.start_line, first.end_line) == ("repo/src/app.py", 10, 12)
    assert first.snippet == "subprocess.run(cmd, shell=True)"


def test_a_document_without_results_is_empty_not_invalid() -> None:
    data = json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "T"}}}]}).encode()
    assert parse_sarif(data).results == ()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not json",
        b"[]",
        b"null",
        b"[" * 200_000,
        b'{"version":"2.0.0","runs":[]}',
        b'{"version":"2.1.0"}',
        b'{"version":"2.1.0","runs":[]}',
        b'{"version":"2.1.0","runs":[{},{}]}',
        b'{"version":"2.1.0","runs":["x"]}',
        b'{"version":"2.1.0","runs":[{"tool":{"driver":{}}}]}',
        b'{"version":"2.1.0","runs":[{"tool":{"driver":{"name":""}}}]}',
        b'{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"T","rules":"x"}}}]}',
        b'{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"T"}},"results":{}}]}',
        b"\xff\xfe",
    ],
)
def test_structurally_invalid_documents_are_rejected(data: bytes) -> None:
    with pytest.raises(ScannerOutputInvalidError):
        parse_sarif(data)


def test_too_many_rules_are_rejected() -> None:
    data = json.dumps(
        {
            "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": "T", "rules": [{}] * (MAX_RULES + 1)}}}],
        }
    ).encode()
    with pytest.raises(ScannerOutputInvalidError):
        parse_sarif(data)


@pytest.mark.parametrize(
    "bad",
    [
        "string",
        None,
        5,
        {},
        {"ruleId": 5, "message": {"text": "m"}},
        {"ruleId": "", "message": {"text": "m"}},
        {"ruleId": "r" * 300, "message": {"text": "m"}},
        {"ruleId": "r"},
        {"ruleId": "r", "message": "m"},
        {"ruleId": "r", "message": {"text": 7}},
        s.result("r", uri="file:///etc/passwd"),
        s.result("r", uri="FILE:///etc/passwd"),
        s.result("r", uri="http://evil/x"),
        s.result("r", uri="%66ile:///etc/passwd"),
        s.result("r", uri="a\\..\\b"),
        s.result("r", uri="a%00b"),
        s.result("r", uri="C:\\Windows"),
        s.result("r", uri=""),
        s.result("r", uri="x" * 5000),
        s.result("r", start=True),
        s.result("r", start="3"),  # type: ignore[arg-type]
        s.result("r", end=1.5),  # type: ignore[arg-type]
    ],
)
def test_malformed_results_are_dropped_and_counted(bad: object) -> None:
    data = s.document([], [bad, s.result("ok")])  # type: ignore[list-item]
    parsed = parse_sarif(data)
    assert [r.rule_id for r in parsed.results] == ["ok"]
    assert parsed.dropped == 1


def test_a_result_without_location_is_kept_without_path() -> None:
    parsed = parse_sarif(s.document([], [s.result("r", uri=None)]))
    assert parsed.results[0].path is None


def test_percent_encoded_traversal_is_decoded_for_the_normalizer_to_reject() -> None:
    parsed = parse_sarif(s.document([], [s.result("r", uri="%2e%2e/%2e%2e/etc/passwd")]))
    assert parsed.results[0].path == "../../etc/passwd"


@pytest.mark.parametrize("score", ["nan", "inf", "-1", "11", "abc", True, [], {}, 1e999])
def test_unusable_severity_scores_are_ignored(score: object) -> None:
    parsed = parse_sarif(s.document([s.rule("r", score=score)], [s.result("r")]))
    assert parsed.results[0].rule is not None
    assert parsed.results[0].rule.security_severity is None


def test_oversized_text_is_truncated_not_trusted() -> None:
    parsed = parse_sarif(s.document([], [s.result("r", "m" * 100_000, snippet="x" * 100_000)]))
    assert len(parsed.results[0].message) == 16_384
    assert parsed.results[0].snippet is not None
    assert len(parsed.results[0].snippet) == 8_192


def test_junk_rule_entries_are_ignored() -> None:
    data = s.document(["x", {"id": 5}, {"id": ""}, {"id": "ok", "properties": "no"}], [])
    assert parse_sarif(data).results == ()


def test_scanner_side_suppressions_are_not_honoured() -> None:
    """Semgrep emite `suppressions` para `# nosemgrep`. Quien controla el código no decide
    qué hallazgos se ven: el triage de NAVE es el único mecanismo de supresión."""
    suppressed = {**s.result("r", uri="repo/a.py"), "suppressions": [{"kind": "inSource"}]}
    parsed = parse_sarif(s.document([], [suppressed]))
    assert [r.rule_id for r in parsed.results] == ["r"]
    assert parsed.dropped == 0
