"""Lectura estricta de SARIF 2.1.0 producido por un escáner (TB6, AM-601, AM-602).

La salida del escáner es entrada no confiable: el escáner corre sobre código del
atacante y puede estar comprometido. Se acepta solo la parte del esquema que NAVE usa;
cualquier resultado mal formado se descarta y se cuenta (el adaptador marca la salida
como incompleta), y un documento que no cumple la estructura mínima se rechaza entero.
No se interpreta nada como código, ni se siguen URIs, ni se resuelven referencias.
"""

import json
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote

from nave.application.errors import ScannerOutputInvalidError

MAX_RULES: Final = 200_000
_MAX_RULE_ID: Final = 256
_MAX_MESSAGE: Final = 16_384
_MAX_SNIPPET: Final = 8_192
_MAX_PATH: Final = 4_096
_MAX_TAGS: Final = 64
_MAX_TAG: Final = 256
_SCHEME: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:", re.ASCII)
_SEVERITY_SCORE_MAX: Final = 10.0


@dataclass(frozen=True, slots=True)
class SarifRule:
    level: str | None
    tags: tuple[str, ...]
    security_severity: float | None


@dataclass(frozen=True, slots=True)
class SarifResult:
    rule_id: str
    rule: SarifRule | None
    level: str | None
    message: str
    path: str | None
    start_line: int | None
    end_line: int | None
    snippet: str | None


@dataclass(frozen=True, slots=True)
class ParsedSarif:
    results: tuple[SarifResult, ...]
    dropped: int
    tool_name: str


def parse_sarif(data: bytes) -> ParsedSarif:
    try:
        document = json.loads(data)
    except (ValueError, RecursionError) as error:
        raise ScannerOutputInvalidError("sarif_not_json") from error
    if not isinstance(document, dict) or document.get("version") != "2.1.0":
        raise ScannerOutputInvalidError("sarif_version")
    runs = document.get("runs")
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        raise ScannerOutputInvalidError("sarif_runs")
    run = runs[0]
    driver = _dict(_dict(run.get("tool")).get("driver"))
    tool_name = driver.get("name")
    if not isinstance(tool_name, str) or not tool_name or len(tool_name) > _MAX_RULE_ID:
        raise ScannerOutputInvalidError("sarif_tool")
    rules = _parse_rules(driver.get("rules"))
    raw_results = run.get("results", [])
    if not isinstance(raw_results, list):
        raise ScannerOutputInvalidError("sarif_results")
    results: list[SarifResult] = []
    dropped = 0
    for raw in raw_results:
        parsed = _parse_result(raw, rules)
        if parsed is None:
            dropped += 1
        else:
            results.append(parsed)
    return ParsedSarif(tuple(results), dropped, tool_name)


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _parse_rules(raw: object) -> dict[str, SarifRule]:
    if raw is None:
        return {}
    if not isinstance(raw, list) or len(raw) > MAX_RULES:
        raise ScannerOutputInvalidError("sarif_rules")
    rules: dict[str, SarifRule] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rule_id = _text(entry.get("id"), _MAX_RULE_ID + 1)
        if not rule_id or len(rule_id) > _MAX_RULE_ID:
            continue
        properties = _dict(entry.get("properties"))
        rules[rule_id] = SarifRule(
            level=_text(_dict(entry.get("defaultConfiguration")).get("level"), 16),
            tags=_tags(properties.get("tags")),
            security_severity=_score(properties.get("security-severity")),
        )
    return rules


def _tags(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    tags = [t[:_MAX_TAG] for t in raw[:_MAX_TAGS] if isinstance(t, str)]
    return tuple(tags)


def _score(raw: object) -> float | None:
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw) if isinstance(raw, int | float | str) else None
    except ValueError:
        return None
    # `nan` y `inf` se rechazan: ningún umbral de severidad puede depender de ellos.
    return value if value is not None and 0.0 <= value <= _SEVERITY_SCORE_MAX else None


def _parse_result(raw: object, rules: dict[str, SarifRule]) -> SarifResult | None:
    if not isinstance(raw, dict):
        return None
    rule_id = _text(raw.get("ruleId"), _MAX_RULE_ID + 1)
    message = _text(_dict(raw.get("message")).get("text"), _MAX_MESSAGE)
    if not rule_id or len(rule_id) > _MAX_RULE_ID or message is None:
        return None
    path: str | None = None
    start = end = None
    snippet = None
    locations = raw.get("locations")
    if isinstance(locations, list) and locations:
        physical = _dict(_dict(locations[0]).get("physicalLocation"))
        if physical:
            path = _uri_to_path(_dict(physical.get("artifactLocation")).get("uri"))
            if path is None:
                return None
            region = _dict(physical.get("region"))
            start, end = _line(region.get("startLine")), _line(region.get("endLine"))
            if (region.get("startLine") is not None and start is None) or (
                region.get("endLine") is not None and end is None
            ):
                return None
            snippet = _text(_dict(region.get("snippet")).get("text"), _MAX_SNIPPET)
    return SarifResult(
        rule_id=rule_id,
        rule=rules.get(rule_id),
        level=_text(raw.get("level"), 16),
        message=message,
        path=path,
        start_line=start,
        end_line=end,
        snippet=snippet,
    )


def _line(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _uri_to_path(uri: object) -> str | None:
    """Solo URIs relativas al repositorio. Un esquema (`file:`, `http:`) o una ruta
    absoluta delatan que el escáner salió del workspace: el resultado se descarta."""
    if not isinstance(uri, str) or not uri or len(uri) > _MAX_PATH:
        return None
    decoded = unquote(uri)
    if _SCHEME.match(uri) or _SCHEME.match(decoded) or "\x00" in decoded or "\\" in decoded:
        return None
    return decoded
