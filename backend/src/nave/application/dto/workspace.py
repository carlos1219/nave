import re
from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str

_HANDLE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", re.ASCII)


@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    """Referencia opaca a un workspace del runner (ADR 0002).

    La aplicación nunca maneja rutas del sistema de ficheros del código escaneado:
    solo este identificador, que el runner resuelve. Así no existe path traversal
    posible en `application`.
    """

    value: str

    def __post_init__(self) -> None:
        if not fullmatch_str(_HANDLE, self.value):
            raise InvalidValueError("workspace", "is not a valid workspace handle")
