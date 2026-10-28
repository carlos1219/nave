"""Validación y saneado de la salida de los escáneres (TB6 del threat model).

Todo lo que llega en un `ScannerOutput` se trata como controlado por el atacante:
rutas, textos, identificadores de regla y referencias. Un resultado que no supera
la validación se descarta y se cuenta; el caso de uso trata un resultado
incompleto como no fiable para dar un PASS (ver `NormalizationResult.incomplete`).
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Final

from nave.application.dto.scanning import NormalizedResult, ScannerOutput
from nave.application.services.fingerprinting import (
    FingerprintKey,
    compute_fingerprint,
    content_hash,
)
from nave.domain.errors import InvalidSourcePathError, InvalidValueError
from nave.domain.validation import fullmatch_str
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import FindingMessage, FindingTitle, Snippet
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.location import SourceLocation
from nave.domain.value_objects.rule_id import CveId, CweId, RuleId
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.source_path import SourcePath

MAX_REFERENCES: Final = 32
_LOCATOR: Final = re.compile(r"[!-~]{1,256}", re.ASCII)
_SECRET_LOCATOR: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedFinding:
    fingerprint: Fingerprint
    scanner_kind: ScannerKind
    category: FindingCategory
    rule_id: RuleId
    severity: Severity
    title: FindingTitle
    cwe: tuple[CweId, ...]
    cve: tuple[CveId, ...]
    location: SourceLocation | None
    message: FindingMessage
    snippet: Snippet | None


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    findings: tuple[NormalizedFinding, ...]
    rejected: int
    truncated: bool

    @property
    def incomplete(self) -> bool:
        """Faltan resultados: la ausencia de un finding no prueba que esté corregido."""
        return self.truncated or self.rejected > 0


def normalize_path(raw: str) -> SourcePath:
    """Forma canónica de una ruta relativa. Rechaza (no corrige) cualquier `..`:
    `normpath` convertiría `a/../../etc` en algo aceptable y ocultaría el intento."""
    if not isinstance(raw, str):
        raise InvalidSourcePathError("path", "must be a string")
    if raw.startswith("/"):
        raise InvalidSourcePathError("path", "must be relative")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if ".." in parts:
        raise InvalidSourcePathError("path", "must not contain '..'")
    return SourcePath("/".join(parts))


def normalize_output(
    output: ScannerOutput, *, max_findings: int, secret_key: FingerprintKey
) -> NormalizationResult:
    if max_findings < 1:
        raise ValueError("max_findings must be positive")
    occurrence_counter: Counter[tuple[str, str, str]] = Counter()
    by_fingerprint: dict[Fingerprint, NormalizedFinding] = {}
    rejected = 0
    # Orden determinista: el índice de ocurrencia de snippets idénticos no depende del
    # orden en que el escáner emita los resultados.
    for result in sorted(output.results, key=_stable_key):
        try:
            finding = _normalize_one(output.kind, result, occurrence_counter, secret_key)
        except InvalidValueError:
            rejected += 1
            continue
        current = by_fingerprint.get(finding.fingerprint)
        if current is None or finding.severity > current.severity:
            by_fingerprint[finding.fingerprint] = finding
    ordered = sorted(
        by_fingerprint.values(),
        key=lambda f: (
            -f.severity.rank,
            f.location.path.value if f.location else "",
            f.rule_id.value,
        ),
    )
    truncated = output.truncated or len(ordered) > max_findings
    return NormalizationResult(
        findings=tuple(ordered[:max_findings]), rejected=rejected, truncated=truncated
    )


def _stable_key(result: NormalizedResult) -> tuple[str, str, int, int, str, str]:
    return (
        str(result.rule_id),
        str(result.path or ""),
        result.start_line if isinstance(result.start_line, int) else 0,
        result.end_line if isinstance(result.end_line, int) else 0,
        str(result.locator),
        str(result.message),
    )


def _normalize_one(
    kind: ScannerKind,
    result: NormalizedResult,
    counter: Counter[tuple[str, str, str]],
    secret_key: FingerprintKey,
) -> NormalizedFinding:
    if not isinstance(result.category, FindingCategory):
        raise InvalidValueError("category", "must be resolved by the adapter")
    if not isinstance(result.severity, Severity):
        raise InvalidValueError("severity", "must be resolved by the adapter")
    rule_id = RuleId(result.rule_id)
    path = normalize_path(result.path) if result.path is not None else None
    location = _location(path, result.start_line, result.end_line)
    locator = _locator(result, rule_id, path, counter)
    return NormalizedFinding(
        fingerprint=compute_fingerprint(
            scanner_kind=kind,
            category=result.category,
            rule_id=rule_id,
            path=path,
            locator=locator,
            key=secret_key,
        ),
        scanner_kind=kind,
        category=result.category,
        rule_id=rule_id,
        severity=result.severity,
        title=FindingTitle.from_untrusted(result.title),
        cwe=_references(result.cwe, CweId),
        cve=_references(result.cve, CveId),
        location=location,
        message=FindingMessage.from_untrusted(result.message),
        # El snippet de un secreto contiene el secreto: nunca se conserva (AM-801).
        snippet=(
            None
            if result.category is FindingCategory.SECRET or result.snippet is None
            else Snippet.from_untrusted(result.snippet)
        ),
    )


def _location(path: SourcePath | None, start: int | None, end: int | None) -> SourceLocation | None:
    if path is None:
        return None
    if start is None:
        return SourceLocation(path)
    return SourceLocation(path, start, end if end is not None else start)


def _locator(
    result: NormalizedResult,
    rule_id: RuleId,
    path: SourcePath | None,
    counter: Counter[tuple[str, str, str]],
) -> str:
    if result.category is FindingCategory.SAST:
        basis = content_hash(result.snippet or result.message)
        key = (rule_id.value, path.value if path else "", basis)
        index = counter[key]
        counter[key] += 1
        return f"{basis}#{index}"
    pattern = _SECRET_LOCATOR if result.category is FindingCategory.SECRET else _LOCATOR
    # Un locator de secreto que no sea un hash podría ser el propio secreto en claro.
    if not fullmatch_str(pattern, result.locator):
        raise InvalidValueError("locator", f"invalid locator for {result.category.value}")
    return result.locator


def _references[R: (CweId, CveId)](raw: tuple[str, ...], kind: type[R]) -> tuple[R, ...]:
    """Las referencias inválidas se descartan sin descartar el finding: son metadatos."""
    valid: list[R] = []
    candidates: tuple[object, ...] = tuple(raw[: MAX_REFERENCES * 4]) if _is_tuple(raw) else ()
    for value in candidates:
        try:
            reference = kind(value)  # type: ignore[arg-type]  # el constructor valida el tipo
        except InvalidValueError:
            continue
        if reference not in valid:
            valid.append(reference)
        if len(valid) == MAX_REFERENCES:
            break
    return tuple(valid)


def _is_tuple(value: object) -> bool:
    return isinstance(value, tuple)
