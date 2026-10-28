"""Errores del dominio de NAVE.

Los mensajes nunca incluyen el valor rechazado: suele venir de una fuente no
confiable (refs de git, nombres de repositorio, salida de escáneres) y acabaría
en logs y respuestas HTTP. Solo se indica el campo y el motivo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from nave.domain.value_objects.scan_status import ScanStatus


class DomainError(Exception):
    """Raíz de todos los errores de dominio. `code` es estable y apto para la API."""

    code: ClassVar[str] = "domain_error"


class InvalidValueError(DomainError):
    code = "invalid_value"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class InvalidIdentifierError(InvalidValueError):
    code = "invalid_identifier"


class InvalidCommitShaError(InvalidValueError):
    code = "invalid_commit_sha"


class InvalidRepoRefError(InvalidValueError):
    code = "invalid_repo_ref"


class InvalidGitRefError(InvalidValueError):
    code = "invalid_git_ref"


class InvalidScannerKindError(InvalidValueError):
    code = "invalid_scanner_kind"


class InvalidFingerprintError(InvalidValueError):
    code = "invalid_fingerprint"


class InvalidTriggerKeyError(InvalidValueError):
    code = "invalid_trigger_key"


class InvalidStatusReasonError(InvalidValueError):
    code = "invalid_status_reason"


class InvalidTimestampError(InvalidValueError):
    code = "invalid_timestamp"


class UnknownEnumValueError(InvalidValueError):
    code = "unknown_enum_value"


class InvalidScanTransitionError(DomainError):
    code = "invalid_scan_transition"

    def __init__(self, current: ScanStatus, target: ScanStatus) -> None:
        super().__init__(f"transition {current.value} -> {target.value} is not allowed")
        self.current = current
        self.target = target


class ScanInvariantError(DomainError):
    """El estado de un `Scan` es incoherente (p. ej. una fila corrupta al rehidratar)."""

    code = "scan_invariant_violation"


class InvalidSourcePathError(InvalidValueError):
    code = "invalid_source_path"


class InvalidRuleIdError(InvalidValueError):
    code = "invalid_rule_id"


class InvalidReferenceError(InvalidValueError):
    code = "invalid_reference"


class InvalidFindingTextError(InvalidValueError):
    code = "invalid_finding_text"


class InvalidQualityGatePolicyError(InvalidValueError):
    code = "invalid_quality_gate_policy"


class InvalidTenantError(InvalidValueError):
    code = "invalid_tenant"


class FindingInvariantError(DomainError):
    code = "finding_invariant_violation"


class InvalidTriageDecisionError(DomainError):
    """La decisión de triage incumple sus reglas (justificación, caducidad, autor)."""

    code = "invalid_triage_decision"


class RedundantTriageDecisionError(DomainError):
    code = "redundant_triage_decision"
