"""Reglas puras del dominio."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from nave.domain.errors import InvalidScanTransitionError
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import ReasonCode

_S = ScanStatus

SCAN_TRANSITIONS: Final[Mapping[ScanStatus, frozenset[ScanStatus]]] = MappingProxyType(
    {
        _S.QUEUED: frozenset({_S.FETCHING, _S.CANCELLED, _S.TIMED_OUT}),
        _S.FETCHING: frozenset({_S.SCANNING, _S.FAILED, _S.CANCELLED, _S.TIMED_OUT}),
        _S.SCANNING: frozenset({_S.PROCESSING, _S.FAILED, _S.CANCELLED, _S.TIMED_OUT}),
        # PROCESSING no se cancela: es corto, transaccional y solo toca la base de datos.
        _S.PROCESSING: frozenset({_S.COMPLETED, _S.FAILED}),
        _S.COMPLETED: frozenset(),
        _S.FAILED: frozenset(),
        _S.CANCELLED: frozenset(),
        _S.TIMED_OUT: frozenset(),
    }
)

_R = ReasonCode

REASONS_BY_STATUS: Final[Mapping[ScanStatus, frozenset[ReasonCode]]] = MappingProxyType(
    {
        _S.FAILED: frozenset(
            {
                _R.FETCH_FAILED,
                _R.REPOSITORY_TOO_LARGE,
                _R.SCANNER_FAILED,
                _R.SCANNER_OUTPUT_INVALID,
                _R.PROCESSING_FAILED,
                _R.INTERNAL_ERROR,
            }
        ),
        _S.TIMED_OUT: frozenset({_R.TIMEOUT, _R.QUEUE_EXPIRED}),
        _S.CANCELLED: frozenset({_R.USER_CANCELLED, _R.SUPERSEDED}),
    }
)


def can_transition(current: ScanStatus, target: ScanStatus) -> bool:
    return target in SCAN_TRANSITIONS[current]


def ensure_scan_transition(current: ScanStatus, target: ScanStatus) -> None:
    if not can_transition(current, target):
        raise InvalidScanTransitionError(current, target)


def allowed_reasons(status: ScanStatus) -> frozenset[ReasonCode]:
    """Códigos de motivo válidos para un estado. Vacío: el estado no lleva motivo."""
    return REASONS_BY_STATUS.get(status, frozenset())
