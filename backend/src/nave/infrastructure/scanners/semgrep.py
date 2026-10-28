"""Semgrep (SAST). Las reglas las provee la plataforma dentro de la imagen del runner."""

import re
from typing import Final

from nave.application.dto.scanning import NormalizedResult
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.infrastructure.sandbox.runner_client import RunnerClient
from nave.infrastructure.scanners.runner_scanner import RunnerScanner
from nave.infrastructure.scanners.sarif import SarifResult

KIND: Final = ScannerKind("semgrep")
_CWE: Final = re.compile(r"CWE-[0-9]{1,6}", re.ASCII)
# Semgrep solo informa error/warning/note. Un nivel desconocido se trata como MEDIUM:
# nunca se infravalora un resultado por no entender su etiqueta.
_SEVERITY: Final = {"error": Severity.HIGH, "warning": Severity.MEDIUM, "note": Severity.LOW}
_UNKNOWN_LEVEL: Final = Severity.MEDIUM
_MAX_CWE: Final = 8
_TARGET_PREFIX: Final = "repo/"
_SKIPPED_RULE_PREFIX: Final = "nave.scan.oversized-"
# Una regla SAST de credenciales codificadas coincide con el propio secreto: el snippet y
# el mensaje (que puede interpolarlo) acabarían persistidos y visibles en la UI.
_SENSITIVE_CWE: Final = frozenset({"CWE-798", "CWE-259", "CWE-321", "CWE-522", "CWE-532"})
_SENSITIVE_RULE: Final = re.compile(
    r"secret|password|passwd|credential|api[-_]?key|token|private[-_]?key", re.IGNORECASE
)
_REDACTED_MESSAGE: Final = "Potential hardcoded credential (details withheld)."


def map_result(result: SarifResult) -> NormalizedResult | None:
    path = _relative_to_repository(result.path)
    if result.path is not None and path is None:
        return None
    level = result.level or (result.rule.level if result.rule else None)
    cwe: list[str] = []
    for tag in result.rule.tags if result.rule else ():
        match = _CWE.match(tag)
        if match and match.group() not in cwe:
            cwe.append(match.group())
    sensitive = bool(_SENSITIVE_CWE.intersection(cwe)) or bool(
        _SENSITIVE_RULE.search(result.rule_id)
    )
    return NormalizedResult(
        rule_id=result.rule_id,
        category=FindingCategory.SAST,
        severity=_SEVERITY.get(level or "", _UNKNOWN_LEVEL),
        title=result.rule_id,
        message=_REDACTED_MESSAGE if sensitive else result.message,
        path=path,
        start_line=result.start_line,
        end_line=result.end_line,
        snippet=None if sensitive else result.snippet,
        cwe=tuple(cwe[:_MAX_CWE]),
    )


def _relative_to_repository(path: str | None) -> str | None:
    """Semgrep informa las rutas relativas a su directorio de trabajo (`/workspace`, fuera
    del árbol del repositorio) y el objetivo es `repo`. Una ruta que no empiece por
    `repo/` apunta fuera del repositorio escaneado y se descarta."""
    if path is None or not path.startswith(_TARGET_PREFIX):
        return None
    return path[len(_TARGET_PREFIX) :]


def reports_skipped_code(result: SarifResult) -> bool:
    """Resultados de plataforma que el envoltorio añade por cada fichero que Semgrep no
    analizó (`nave_oversized.py`, ADR 0024): la salida es incompleta."""
    return result.rule_id.startswith(_SKIPPED_RULE_PREFIX)


def semgrep_scanner(client: RunnerClient, *, expected_version: str) -> RunnerScanner:
    return RunnerScanner(
        kind=KIND,
        expected_version=expected_version,
        client=client,
        mapper=map_result,
        incomplete_when=reports_skipped_code,
    )
