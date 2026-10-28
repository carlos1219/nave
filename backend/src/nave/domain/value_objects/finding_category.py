from enum import Enum
from typing import Self

from nave.domain.value_objects.enum_parsing import parse_enum


class FindingCategory(Enum):
    """Tipo de hallazgo. Determina cómo se calcula el fingerprint."""

    SAST = "SAST"
    DEPENDENCY = "DEPENDENCY"
    SECRET = "SECRET"  # noqa: S105 - categoría de hallazgo, no una contraseña
    IAC = "IAC"

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "finding_category")
