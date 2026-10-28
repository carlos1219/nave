"""Documentos SARIF con la forma que producen Semgrep y Trivy (campos que NAVE lee)."""

import json
from typing import Any


def document(
    rules: list[dict[str, Any]], results: list[dict[str, Any]], *, name: str = "Tool"
) -> bytes:
    return json.dumps(
        {
            "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": name, "rules": rules}}, "results": results}],
        }
    ).encode()


def result(  # noqa: PLR0913 - builder de fixtures
    rule_id: str,
    message: str = "message",
    *,
    uri: str | None = "src/app.py",
    start: int | None = 10,
    end: int | None = 12,
    level: str | None = "error",
    snippet: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ruleId": rule_id, "message": {"text": message}}
    if level is not None:
        out["level"] = level
    if uri is not None:
        region: dict[str, Any] = {}
        if start is not None:
            region["startLine"] = start
        if end is not None:
            region["endLine"] = end
        if snippet is not None:
            region["snippet"] = {"text": snippet}
        out["locations"] = [
            {"physicalLocation": {"artifactLocation": {"uri": uri}, "region": region}}
        ]
    return out


def rule(
    rule_id: str, *tags: str, level: str | None = None, score: object = None
) -> dict[str, Any]:
    properties: dict[str, Any] = {"tags": list(tags)}
    if score is not None:
        properties["security-severity"] = score
    out: dict[str, Any] = {"id": rule_id, "properties": properties}
    if level is not None:
        out["defaultConfiguration"] = {"level": level}
    return out


def semgrep_sample() -> bytes:  # rutas como las emite Semgrep: relativas a /workspace
    return document(
        [
            rule(
                "python.lang.security.audit.subprocess-shell-true",
                "CWE-78: Improper Neutralization of Special Elements used in an OS Command",
                "OWASP-A03:2021 - Injection",
                level="error",
            ),
            rule("generic.todo", level="note"),
        ],
        [
            result(
                "python.lang.security.audit.subprocess-shell-true",
                "Found 'subprocess' with shell=True",
                uri="repo/src/app.py",
                snippet="subprocess.run(cmd, shell=True)",
            ),
            result(
                "generic.todo",
                "TODO left in code",
                uri="repo/src/app.py",
                level="note",
                start=3,
                end=3,
            ),
        ],
        name="Semgrep OSS",
    )


TRIVY_VULN = (
    "Package: lodash\nInstalled Version: 4.17.20\nVulnerability CVE-2021-23337\n"
    "Severity: HIGH\nFixed Version: 4.17.21\nLink: https://avd.aquasec.com/nvd/cve-2021-23337"
)
TRIVY_SECRET = (
    "Artifact: config/.env\nSecret AWS Access Key ID\nSeverity: CRITICAL\n"
    "Match: AWS_KEY=****************MPLE"
)
TRIVY_MISCONFIG = (
    "Artifact: main.tf\nType: terraform\nVulnerability AVD-AWS-0086\n"
    "Severity: HIGH\nMessage: Bucket has public access"
)


def trivy_sample() -> bytes:
    return document(
        [
            rule("CVE-2021-23337", "vulnerability", "security", "HIGH", score="7.2"),
            rule("aws-access-key-id", "secret", "security", "CRITICAL"),
            rule("AVD-AWS-0086", "misconfiguration", "security", "HIGH"),
        ],
        [
            result("CVE-2021-23337", TRIVY_VULN, uri="package-lock.json", start=1, end=1),
            result(
                "aws-access-key-id", TRIVY_SECRET, uri="config/.env", start=3, end=3, level="error"
            ),
            result("AVD-AWS-0086", TRIVY_MISCONFIG, uri="main.tf", start=5, end=9),
        ],
        name="Trivy",
    )
