import re
from dataclasses import dataclass
from typing import Final, Self

from nave.domain.errors import InvalidRepoRefError
from nave.domain.validation import fullmatch_str

# Reglas de GitHub para cuentas: alfanumérico y guiones simples, sin guion al inicio
# ni al final, máximo 39 caracteres.
_OWNER: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}", re.ASCII)
_NAME: Final = re.compile(r"[A-Za-z0-9._-]{1,100}", re.ASCII)
_RESERVED_NAMES: Final = frozenset({".", ".."})


def is_valid_account_login(value: object) -> bool:
    """Login de cuenta u organización de GitHub."""
    return fullmatch_str(_OWNER, value)


@dataclass(frozen=True, slots=True)
class RepoRef:
    """Repositorio de GitHub como `owner/name`.

    Es la única forma de designar un repositorio fuera de la base de datos: la URL de
    clonado se construye a partir de él y nunca se toma del payload del webhook (AM-206).
    """

    owner: str
    name: str

    def __post_init__(self) -> None:
        if not is_valid_account_login(self.owner):
            raise InvalidRepoRefError("owner", "is not a valid GitHub account name")
        if not fullmatch_str(_NAME, self.name) or self.name in _RESERVED_NAMES:
            raise InvalidRepoRefError("name", "is not a valid GitHub repository name")

    @classmethod
    def parse(cls, full_name: str) -> Self:
        if not isinstance(full_name, str):
            raise InvalidRepoRefError("full_name", "must be a string")
        if full_name.count("/") != 1:
            raise InvalidRepoRefError("full_name", "must have the form owner/name")
        owner, name = full_name.split("/")
        return cls(owner=owner, name=name)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def __str__(self) -> str:
        return self.full_name
