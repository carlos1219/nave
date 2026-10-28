"""Exportación a SARIF 2.1.0 para Code Scanning (CLAUDE.md §3, §9; ADR 0014).

Entrada: `FindingView` ya normalizados (secretos redactados al normalizar, AM-801).
Salida: SARIF comprimido con gzip y en base64, dentro del límite de GitHub.

Todo texto que viene del repositorio escaneado (mensajes, títulos) puede llegar a
interpretarse como Markdown en la interfaz de GitHub, donde un enlace o una imagen
inyectados serían una vía de ataque (AM-802): se emite siempre dentro de un code span.
Los hallazgos de secretos no llevan mensaje ni fragmento, solo título, regla y ruta.
"""

import base64
import gzip
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote

from nave.application.dto.views import FindingView
from nave.application.services.pr_comment import code_span
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity

SARIF_SCHEMA: Final = "https://json.schemastore.org/sarif-2.1.0.json"
# GitHub rechaza ficheros de más de 10 MB comprimidos; el margen cubre el base64 del
# transporte y variaciones de la compresión.
MAX_GZIP_BYTES: Final = 9 * 1024 * 1024
MAX_RESULTS: Final = 25_000
MAX_MESSAGE: Final = 1000
_SHRINK_NUMERATOR: Final = 3
_SHRINK_DENOMINATOR: Final = 4

_LEVEL: Final = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "note",
}
# Valores centrales de cada banda de GitHub (crítico > 9.0, alto 7.0-8.9, medio 4.0-6.9,
# bajo 0.1-3.9). Informativo se omite: 0 significa "sin severidad de seguridad".
_SECURITY_SEVERITY: Final = {
    Severity.CRITICAL: "9.5",
    Severity.HIGH: "8.0",
    Severity.MEDIUM: "5.5",
    Severity.LOW: "2.0",
}


@dataclass(frozen=True, slots=True)
class SarifPayload:
    encoded: str
    """SARIF en gzip y base64, listo para `POST /code-scanning/sarifs`."""
    included: int
    omitted: int
    """Hallazgos que no caben (límite de tamaño o de resultados) o sin ubicación."""


def build_sarif_payload(findings: Sequence[FindingView]) -> SarifPayload | None:
    """`None` si no queda ningún resultado que subir."""
    usable = [v for v in findings if v.occurrence is not None and v.occurrence.location is not None]
    ordered = sorted(
        usable,
        key=lambda v: (
            -v.finding.severity.rank,
            v.finding.rule_id.value,
            v.occurrence.location.path.value,  # type: ignore[union-attr]
            v.finding.fingerprint.value,
        ),
    )[:MAX_RESULTS]
    count = len(ordered)
    while count > 0:
        compressed = gzip.compress(_serialize(ordered[:count]), compresslevel=6, mtime=0)
        if len(compressed) <= MAX_GZIP_BYTES:
            encoded = base64.b64encode(compressed).decode("ascii")
            return SarifPayload(encoded, count, len(findings) - count)
        count = count * _SHRINK_NUMERATOR // _SHRINK_DENOMINATOR
    return None


def sarif_document(findings: Sequence[FindingView]) -> tuple[bytes, int]:
    """SARIF sin comprimir para descargar (ADR 0029) y cuántos resultados lleva. Mismas
    reglas que la subida a Code Scanning: solo hallazgos con ubicación, en code spans."""
    usable = [v for v in findings if v.occurrence is not None and v.occurrence.location is not None]
    chosen = usable[:MAX_RESULTS]
    return _serialize(chosen), len(chosen)


def _serialize(views: Sequence[FindingView]) -> bytes:
    by_scanner: dict[ScannerKind, list[FindingView]] = {}
    for view in views:
        by_scanner.setdefault(view.finding.scanner_kind, []).append(view)
    runs = [
        _run(kind, group) for kind, group in sorted(by_scanner.items(), key=lambda i: i[0].value)
    ]
    document = {"$schema": SARIF_SCHEMA, "version": "2.1.0", "runs": runs}
    return json.dumps(document, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _run(kind: ScannerKind, views: Sequence[FindingView]) -> dict[str, object]:
    rules: dict[str, dict[str, object]] = {}
    results: list[dict[str, object]] = []
    for view in views:
        finding = view.finding
        rule_id = finding.rule_id.value
        rule = rules.setdefault(rule_id, _rule(view))
        _raise_rule_severity(rule, finding.severity)
        results.append(_result(view))
    run: dict[str, object] = {
        "tool": {"driver": {"name": f"NAVE {kind.value}", "rules": list(rules.values())}},
        "automationDetails": {"id": f"nave/{kind.value}/"},
        "results": results,
    }
    _finalize_rules(run)
    return run


def _rule(view: FindingView) -> dict[str, object]:
    finding = view.finding
    tags = ["security", *(f"external/cwe/{c.value.lower()}" for c in finding.cwe)]
    properties: dict[str, object] = {"tags": tags}
    return {
        "id": finding.rule_id.value,
        "shortDescription": {"text": code_span(finding.title.value, MAX_MESSAGE)},
        "properties": properties,
        "_severity": finding.severity,
    }


def _raise_rule_severity(rule: dict[str, object], severity: Severity) -> None:
    current = rule["_severity"]
    if isinstance(current, Severity) and severity > current:
        rule["_severity"] = severity


def _finalize_rules(run: dict[str, object]) -> None:
    driver = run["tool"]["driver"]  # type: ignore[index]
    for rule in driver["rules"]:
        severity = rule.pop("_severity")
        score = _SECURITY_SEVERITY.get(severity)
        if score is not None:
            rule["properties"]["security-severity"] = score


def _result(view: FindingView) -> dict[str, object]:
    finding, occurrence = view.finding, view.occurrence
    if occurrence is None or occurrence.location is None:  # filtrado antes; defensa
        raise ValueError("finding without location")
    location = occurrence.location
    region = {
        "startLine": location.start_line or 1,
        "endLine": location.end_line or location.start_line or 1,
    }
    if finding.category is FindingCategory.SECRET:
        text = finding.title.value
    else:
        text = occurrence.message.value or finding.title.value
    fingerprint = finding.fingerprint.value
    return {
        "ruleId": finding.rule_id.value,
        "level": _LEVEL[finding.severity],
        "message": {"text": code_span(text, MAX_MESSAGE)},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": quote(location.path.value, safe="/")},
                    "region": region,
                }
            }
        ],
        # GitHub solo usa `primaryLocationLineHash` para seguir un resultado entre commits.
        # Se rellena con el fingerprint estable de NAVE (independiente de la línea).
        "partialFingerprints": {
            "primaryLocationLineHash": f"{fingerprint[:32]}:1",
            "nave/v1": fingerprint,
        },
    }
