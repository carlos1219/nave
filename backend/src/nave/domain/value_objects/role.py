from enum import Enum
from typing import Final, Self

from nave.domain.value_objects.enum_parsing import parse_enum


class Role(Enum):
    """Roles RBAC por organización. Cada rol incluye los permisos de los inferiores."""

    VIEWER = "VIEWER"
    TRIAGER = "TRIAGER"
    ADMIN = "ADMIN"

    def satisfies(self, required: "Role") -> bool:
        return _RANK[self] >= _RANK[required]

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "role")


_RANK: Final = {Role.VIEWER: 0, Role.TRIAGER: 1, Role.ADMIN: 2}
