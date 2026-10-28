import re
from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str, is_int_in_range
from nave.domain.value_objects.scanner_kind import ScannerKind

_VERSION: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", re.ASCII)
_IMAGE_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
MAX_FINDINGS: Final = 10_000_000


@dataclass(frozen=True, slots=True)
class ToolRun:
    """Qué escáner, en qué versión e imagen, analizó un escaneo y cuántos hallazgos dio.

    Permite saber si un resultado limpio procede de un escáner al día (ADR 0029)."""

    scanner: ScannerKind
    version: str
    image_digest: str
    findings: int

    def __post_init__(self) -> None:
        if not isinstance(self.scanner, ScannerKind):
            raise InvalidValueError("tool.scanner", "must be a ScannerKind")
        if not fullmatch_str(_VERSION, self.version):
            raise InvalidValueError("tool.version", "is not a valid version string")
        if not fullmatch_str(_IMAGE_DIGEST, self.image_digest):
            raise InvalidValueError("tool.image_digest", "must be sha256:<64 hex>")
        if not is_int_in_range(self.findings, 0, MAX_FINDINGS):
            raise InvalidValueError("tool.findings", f"must be in [0, {MAX_FINDINGS}]")
