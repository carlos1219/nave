import re
from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidFingerprintError
from nave.domain.validation import fullmatch_str

_PATTERN: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """Huella estable de un finding (SHA-256 en hexadecimal).

    Aquí solo se valida la forma. El algoritmo vive en `application/services`
    (ver docs/architecture/puertos-y-contratos.md §5).
    """

    value: str

    def __post_init__(self) -> None:
        if not fullmatch_str(_PATTERN, self.value):
            raise InvalidFingerprintError(
                "fingerprint", "must be 64 lowercase hexadecimal characters"
            )

    def __str__(self) -> str:
        return self.value
