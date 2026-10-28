"""Trivy (SCA, secretos, IaC). La base de datos de vulnerabilidades la provee la
plataforma (mirror controlado, ADR 0007); el escáner no descarga nada al ejecutarse."""

import hashlib
import re
from typing import Final

from nave.application.dto.scanning import NormalizedResult
from nave.application.services.fingerprinting import content_hash
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.infrastructure.sandbox.runner_client import RunnerClient
from nave.infrastructure.scanners.runner_scanner import RunnerScanner
from nave.infrastructure.scanners.sarif import SarifResult, SarifRule

KIND: Final = ScannerKind("trivy")
_CVE: Final = re.compile(r"CVE-[0-9]{4}-[0-9]{4,19}", re.ASCII)
_PACKAGE_LINE: Final = re.compile(r"^Package: *(\S{1,200})$", re.MULTILINE | re.ASCII)
_UNSAFE_LOCATOR: Final = re.compile(r"[^A-Za-z0-9._:/@+-]", re.ASCII)
_CATEGORY_TAGS: Final = {
    "vulnerability": FindingCategory.DEPENDENCY,
    "misconfiguration": FindingCategory.IAC,
    "secret": FindingCategory.SECRET,
}
_SEVERITY_TAGS: Final = {s.value: s for s in Severity if s is not Severity.INFO} | {
    "UNKNOWN": Severity.INFO
}
_LEVELS: Final = {"error": Severity.HIGH, "warning": Severity.MEDIUM, "note": Severity.LOW}
_CVSS_CRITICAL: Final = 9.0
_CVSS_HIGH: Final = 7.0
_CVSS_MEDIUM: Final = 4.0
_LOCATOR_HASH_CHARS: Final = 32


def _category(rule: SarifRule | None) -> FindingCategory | None:
    for tag in rule.tags if rule else ():
        category = _CATEGORY_TAGS.get(tag.lower())
        if category is not None:
            return category
    return None


def _severity(result: SarifResult) -> Severity:
    rule = result.rule
    for tag in rule.tags if rule else ():
        severity = _SEVERITY_TAGS.get(tag)
        if severity is not None:
            return severity
    score = rule.security_severity if rule else None
    if score is not None:
        if score >= _CVSS_CRITICAL:
            return Severity.CRITICAL
        if score >= _CVSS_HIGH:
            return Severity.HIGH
        if score >= _CVSS_MEDIUM:
            return Severity.MEDIUM
        return Severity.LOW if score > 0 else Severity.INFO
    return _LEVELS.get(result.level or "", Severity.MEDIUM)


def _locator(category: FindingCategory, result: SarifResult) -> str:
    if category is FindingCategory.SECRET:
        # El mensaje de Trivy lleva el valor ya censurado; el secreto en claro no existe
        # en esta salida. Además, el fingerprint se firma con HMAC (AM-805).
        return hashlib.sha256(result.message.encode("utf-8", "surrogatepass")).hexdigest()
    if category is FindingCategory.DEPENDENCY:
        match = _PACKAGE_LINE.search(result.message)
        name = _UNSAFE_LOCATOR.sub("_", match.group(1)) if match else "unknown"
        return f"pkg:{name}"
    return f"msg:{content_hash(result.message)[:_LOCATOR_HASH_CHARS]}"


def map_result(result: SarifResult) -> NormalizedResult | None:
    category = _category(result.rule)
    if category is None:
        return None
    return NormalizedResult(
        rule_id=result.rule_id,
        category=category,
        severity=_severity(result),
        title=result.rule_id,
        message=result.message,
        path=result.path,
        start_line=result.start_line,
        end_line=result.end_line,
        locator=_locator(category, result),
        cve=(result.rule_id,) if _CVE.fullmatch(result.rule_id) else (),
    )


def trivy_scanner(client: RunnerClient, *, expected_version: str) -> RunnerScanner:
    return RunnerScanner(
        kind=KIND, expected_version=expected_version, client=client, mapper=map_result
    )
