"""Codificación JSON de valores del dominio para columnas JSONB.

Lo que se lee de la base de datos se trata como no confiable (puede venir de una fila
corrupta o manipulada): cada decodificador valida tipos y forma y reconstruye el valor
a través de sus constructores, que vuelven a aplicar las invariantes del dominio.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Final
from uuid import UUID

from nave.domain.errors import InvalidValueError
from nave.domain.events import DomainEvent
from nave.domain.quality_gates import (
    Gate,
    GateScope,
    PolicyVerdict,
    QualityGatePolicy,
    ReasonKind,
    Verdict,
    VerdictReason,
)
from nave.domain.value_objects.enum_parsing import parse_enum
from nave.domain.value_objects.identifiers import EntityId
from nave.domain.value_objects.scan_summary import MAX_TOOLS, ScanSummary
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.status_reason import StatusReason
from nave.domain.value_objects.tool_run import ToolRun

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None

MAX_REASONS: Final = 256


def _object(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise InvalidValueError(field, "must be a JSON object")
    return raw


def _keys(raw: Mapping[str, object], field: str, allowed: frozenset[str]) -> None:
    if not set(raw) <= allowed:
        raise InvalidValueError(field, "contains unknown keys")


def _int(raw: object, field: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise InvalidValueError(field, "must be an integer")
    return raw


def _str(raw: object, field: str) -> str:
    if not isinstance(raw, str):
        raise InvalidValueError(field, "must be a string")
    return raw


def _bool(raw: object, field: str) -> bool:
    if not isinstance(raw, bool):
        raise InvalidValueError(field, "must be a boolean")
    return raw


# --- Resumen de escaneo -------------------------------------------------------------

_COUNT_KEYS: Final = frozenset(s.value.lower() for s in Severity)


def encode_counts(counts: SeverityCounts) -> dict[str, Json]:
    return {s.value.lower(): counts.get(s) for s in Severity}


def decode_counts(raw: object, field: str) -> SeverityCounts:
    data = _object(raw, field)
    _keys(data, field, _COUNT_KEYS)
    return SeverityCounts(**{key: _int(value, field) for key, value in data.items()})


_TOOL_KEYS: Final = frozenset({"scanner", "version", "image_digest", "findings"})


def encode_summary(summary: ScanSummary) -> dict[str, Json]:
    encoded: dict[str, Json] = {
        "new": encode_counts(summary.new),
        "open": encode_counts(summary.open),
        "verdict": summary.verdict.value,
        "truncated": summary.truncated,
    }
    if summary.tools:
        encoded["tools"] = [
            {
                "scanner": t.scanner.value,
                "version": t.version,
                "image_digest": t.image_digest,
                "findings": t.findings,
            }
            for t in summary.tools
        ]
    return encoded


def _decode_tool(raw: object) -> ToolRun:
    data = _object(raw, "summary.tools")
    _keys(data, "summary.tools", _TOOL_KEYS)
    return ToolRun(
        scanner=ScannerKind(_str(data.get("scanner"), "summary.tools.scanner")),
        version=_str(data.get("version"), "summary.tools.version"),
        image_digest=_str(data.get("image_digest"), "summary.tools.image_digest"),
        findings=_int(data.get("findings"), "summary.tools.findings"),
    )


def decode_summary(raw: object) -> ScanSummary:
    data = _object(raw, "summary")
    _keys(data, "summary", frozenset({"new", "open", "verdict", "truncated", "tools"}))
    tools = data.get("tools", [])
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        raise InvalidValueError("summary.tools", f"must be a list of at most {MAX_TOOLS} runs")
    return ScanSummary(
        new=decode_counts(data.get("new"), "summary.new"),
        open=decode_counts(data.get("open"), "summary.open"),
        verdict=Verdict.parse(_str(data.get("verdict"), "summary.verdict")),
        truncated=_bool(data.get("truncated"), "summary.truncated"),
        tools=tuple(_decode_tool(t) for t in tools),
    )


# --- Veredictos ---------------------------------------------------------------------


def encode_reasons(verdict: PolicyVerdict) -> list[Json]:
    return [
        {
            "kind": reason.kind.value,
            "rule": reason.rule,
            "blocking": reason.blocking,
            "severity": reason.severity.value if reason.severity is not None else None,
            "observed": reason.observed,
            "limit": reason.limit,
        }
        for reason in verdict.reasons
    ]


def decode_verdict(verdict: str, raw_reasons: object) -> PolicyVerdict:
    if not isinstance(raw_reasons, list) or len(raw_reasons) > MAX_REASONS:
        raise InvalidValueError("reasons", f"must be a list of at most {MAX_REASONS}")
    reasons = []
    for raw in raw_reasons:
        data = _object(raw, "reason")
        _keys(
            data, "reason", frozenset({"kind", "rule", "blocking", "severity", "observed", "limit"})
        )
        severity = data.get("severity")
        reasons.append(
            VerdictReason(
                kind=parse_enum(ReasonKind, data.get("kind"), "reason.kind"),
                rule=_str(data.get("rule"), "reason.rule"),
                blocking=_bool(data.get("blocking"), "reason.blocking"),
                severity=None if severity is None else Severity.parse(_str(severity, "severity")),
                observed=_int(data.get("observed"), "reason.observed"),
                limit=_int(data.get("limit"), "reason.limit"),
            )
        )
    return PolicyVerdict(Verdict.parse(verdict), tuple(reasons))


# --- Documento de política (ADR 0003) -----------------------------------------------


def _limits(raw: object, field: str) -> dict[Severity, int]:
    data = _object(raw, field)
    return {Severity.parse(key): _int(value, field) for key, value in data.items()}


def encode_policy(policy: QualityGatePolicy) -> dict[str, Json]:
    return {
        "version": policy.version,
        "gates": [
            {
                "id": gate.id,
                "scope": gate.scope.value.lower(),
                "max": {s.value: n for s, n in gate.max_by_severity.items()},
            }
            for gate in policy.gates
        ],
        "sla_days": {s.value: n for s, n in policy.sla_days.items()},
        "fail_on_sla_breach": policy.fail_on_sla_breach,
    }


def decode_policy(raw: object) -> QualityGatePolicy:
    data = _object(raw, "policy")
    _keys(data, "policy", frozenset({"version", "gates", "sla_days", "fail_on_sla_breach"}))
    raw_gates = data.get("gates")
    if not isinstance(raw_gates, list):
        raise InvalidValueError("policy.gates", "must be a list")
    gates = []
    for raw_gate in raw_gates:
        gate = _object(raw_gate, "gate")
        _keys(gate, "gate", frozenset({"id", "scope", "max"}))
        gates.append(
            Gate(
                _str(gate.get("id"), "gate.id"),
                GateScope.parse(_str(gate.get("scope"), "gate.scope")),
                _limits(gate.get("max"), "gate.max"),
            )
        )
    return QualityGatePolicy(
        version=_int(data.get("version"), "policy.version"),
        gates=tuple(gates),
        sla_days=_limits(data.get("sla_days", {}), "policy.sla_days"),
        fail_on_sla_breach=_bool(data.get("fail_on_sla_breach", False), "fail_on_sla_breach"),
    )


# --- Eventos de dominio (outbox) ----------------------------------------------------


def _encode_value(value: object) -> Json:  # noqa: PLR0911 - un caso por tipo
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, EntityId):
        return str(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _encode_value(value.value)
    if isinstance(value, StatusReason):
        return {"code": value.code.value, "detail": value.detail}
    inner = getattr(value, "value", None)
    if is_dataclass(value) and isinstance(inner, str):
        return inner
    raise TypeError(f"cannot encode {type(value).__name__} for the outbox")


def encode_event(event: DomainEvent) -> tuple[str, dict[str, Json]]:
    """`(tipo, payload)`. El tipo es el nombre de la clase del evento: contrato estable
    con los consumidores del outbox; renombrar un evento exige versionarlo."""
    payload = {f.name: _encode_value(getattr(event, f.name)) for f in fields(event)}
    return type(event).__name__, payload
