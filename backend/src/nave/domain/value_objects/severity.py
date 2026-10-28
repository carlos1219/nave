from enum import Enum
from functools import total_ordering
from typing import Final, Self

from nave.domain.value_objects.enum_parsing import parse_enum


@total_ordering
class Severity(Enum):
    """Severidad normalizada. Ordenable: `Severity.CRITICAL > Severity.HIGH`.

    Es un `Enum` y no un `StrEnum` a propósito: `StrEnum` heredaría el orden
    alfabético de `str`, y `"HIGH" < "LOW"` es justo lo contrario de lo correcto.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank < other.rank

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "severity")


_RANK: Final = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
