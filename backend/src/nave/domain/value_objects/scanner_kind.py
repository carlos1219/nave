import re
from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidScannerKindError
from nave.domain.validation import fullmatch_str

_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{0,30}[a-z0-9]", re.ASCII)


@dataclass(frozen=True, slots=True)
class ScannerKind:
    """Identificador de un escáner (`semgrep`, `trivy`, ...).

    Es un value object validado y no un `Enum`: añadir un escáner no debe tocar el
    dominio (CLAUDE.md §3, §5.4). El catálogo de escáneres existentes es el
    `ScannerRegistry` de infraestructura.
    """

    value: str

    def __post_init__(self) -> None:
        if not fullmatch_str(_PATTERN, self.value):
            raise InvalidScannerKindError(
                "scanner_kind", "must be 2-32 lowercase letters, digits or '-'"
            )

    def __str__(self) -> str:
        return self.value
