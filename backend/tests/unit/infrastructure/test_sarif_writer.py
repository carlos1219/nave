import base64
import gzip
import hashlib
import json
from typing import Any

import pytest

from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.severity import Severity
from nave.infrastructure.vcs import sarif_writer
from nave.infrastructure.vcs.sarif_writer import build_sarif_payload
from tests.fakes.findings import make_view


def noise(i: int) -> str:
    return "".join(hashlib.sha256(f"{i}-{j}".encode()).hexdigest() for j in range(10))


def decode(encoded: str) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(gzip.decompress(base64.b64decode(encoded)))
    return decoded


def test_empty_or_locationless_yields_none() -> None:
    assert build_sarif_payload([]) is None
    assert build_sarif_payload([make_view(path=None)]) is None


def test_document_shape_and_levels() -> None:
    payload = build_sarif_payload(
        [
            make_view(1, severity=Severity.CRITICAL, rule="r.a"),
            make_view(2, severity=Severity.MEDIUM, rule="r.b"),
            make_view(3, severity=Severity.INFO, rule="r.c"),
        ]
    )
    assert payload is not None
    assert (payload.included, payload.omitted) == (3, 0)
    doc = decode(payload.encoded)
    assert doc["version"] == "2.1.0"
    (run,) = doc["runs"]
    assert run["tool"]["driver"]["name"] == "NAVE semgrep"
    assert run["automationDetails"]["id"] == "nave/semgrep/"
    assert [r["level"] for r in run["results"]] == ["error", "warning", "note"]
    rules = {r["id"]: r["properties"] for r in run["tool"]["driver"]["rules"]}
    assert rules["r.a"]["security-severity"] == "9.5"
    assert "security-severity" not in rules["r.c"]
    assert all("_severity" not in r for r in run["tool"]["driver"]["rules"])


def test_one_run_per_scanner_and_stable_fingerprints() -> None:
    payload = build_sarif_payload([make_view(1, kind="semgrep"), make_view(2, kind="trivy")])
    assert payload is not None
    doc = decode(payload.encoded)
    assert [r["tool"]["driver"]["name"] for r in doc["runs"]] == ["NAVE semgrep", "NAVE trivy"]
    fps = doc["runs"][0]["results"][0]["partialFingerprints"]
    assert fps["nave/v1"] == f"{1:064x}"
    assert fps["primaryLocationLineHash"] == f"{1:064x}"[:32] + ":1"


def test_rule_severity_is_highest_seen() -> None:
    payload = build_sarif_payload(
        [make_view(1, severity=Severity.LOW), make_view(2, severity=Severity.HIGH)]
    )
    assert payload is not None
    (rule,) = decode(payload.encoded)["runs"][0]["tool"]["driver"]["rules"]
    assert rule["properties"]["security-severity"] == "8.0"


def test_message_is_code_span_and_cannot_inject_markdown() -> None:
    hostile = "[click](http://evil) ![x](http://evil/i.png) `` ` <img src=x>"
    payload = build_sarif_payload([make_view(message=hostile, title=hostile)])
    assert payload is not None
    result = decode(payload.encoded)["runs"][0]["results"][0]
    text = result["message"]["text"]
    assert text.startswith("`")
    assert text.endswith("`")
    assert "\n" not in text


def test_secret_findings_carry_title_only() -> None:
    payload = build_sarif_payload(
        [make_view(category=FindingCategory.SECRET, title="AWS key", message="AKIA1234567890")]
    )
    assert payload is not None
    text = decode(payload.encoded)["runs"][0]["results"][0]["message"]["text"]
    assert "AKIA" not in text
    assert "AWS key" in text


def test_unknown_line_defaults_to_one_and_path_is_quoted() -> None:
    payload = build_sarif_payload([make_view(path="dir with space/é.py", line=None)])
    assert payload is not None
    loc = decode(payload.encoded)["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert loc["region"] == {"startLine": 1, "endLine": 1}
    assert loc["artifactLocation"]["uri"] == "dir%20with%20space/%C3%A9.py"


def test_output_is_deterministic_and_ordered_by_severity() -> None:
    views = [make_view(1, severity=Severity.LOW), make_view(2, severity=Severity.CRITICAL)]
    a, b = build_sarif_payload(views), build_sarif_payload(list(reversed(views)))
    assert a is not None
    assert b is not None
    assert a.encoded == b.encoded
    assert decode(a.encoded)["runs"][0]["results"][0]["level"] == "error"


def test_result_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sarif_writer, "MAX_RESULTS", 2)
    payload = build_sarif_payload([make_view(i) for i in range(1, 6)])
    assert payload is not None
    assert (payload.included, payload.omitted) == (2, 3)


def test_shrinks_until_it_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sarif_writer, "MAX_GZIP_BYTES", 6000)
    views = [make_view(i, message=noise(i), path=f"p{i}/f.py") for i in range(1, 60)]
    payload = build_sarif_payload(views)
    assert payload is not None
    assert 0 < payload.included < len(views)
    assert len(gzip.decompress(base64.b64decode(payload.encoded))) > 0
    assert len(base64.b64decode(payload.encoded)) <= 6000


def test_nothing_fits_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sarif_writer, "MAX_GZIP_BYTES", 10)
    assert build_sarif_payload([make_view()]) is None
