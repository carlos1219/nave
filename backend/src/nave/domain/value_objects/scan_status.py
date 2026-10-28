from enum import Enum
from typing import Self

from nave.domain.value_objects.enum_parsing import parse_enum


class ScanStatus(Enum):
    """Estados de un escaneo. Las transiciones permitidas viven en `nave.domain.policies`."""

    QUEUED = "QUEUED"
    FETCHING = "FETCHING"
    SCANNING = "SCANNING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ScanStatus.COMPLETED,
            ScanStatus.FAILED,
            ScanStatus.CANCELLED,
            ScanStatus.TIMED_OUT,
        }

    @property
    def is_running(self) -> bool:
        return self in {ScanStatus.FETCHING, ScanStatus.SCANNING, ScanStatus.PROCESSING}

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "scan_status")
