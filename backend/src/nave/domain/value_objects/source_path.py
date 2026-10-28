import re
from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidSourcePathError
from nave.domain.value_objects.text import is_safe_text

MAX_PATH_LENGTH: Final = 1024
_WINDOWS_DRIVE: Final = re.compile(r"[A-Za-z]:", re.ASCII)
_FORBIDDEN_COMPONENTS: Final = frozenset({"", ".", ".."})


def _violation(value: str) -> str | None:  # noqa: PLR0911 - una regla por retorno
    if not value:
        return "must not be empty"
    if len(value) > MAX_PATH_LENGTH:
        return f"must not exceed {MAX_PATH_LENGTH} characters"
    if not is_safe_text(value):
        return "contains control or format characters"
    if "\\" in value:
        return "must use '/' as separator"
    if value.startswith("/"):
        return "must be relative"
    parts = value.split("/")
    if _WINDOWS_DRIVE.match(parts[0]):
        return "must be relative"
    if any(part in _FORBIDDEN_COMPONENTS for part in parts):
        return "must not contain empty, '.' or '..' components"
    return None


@dataclass(frozen=True, slots=True)
class SourcePath:
    """Ruta de un fichero dentro del repositorio, relativa y en forma canónica.

    Es un dato que se muestra, nunca una ruta que se abra: NAVE no lee ficheros del
    repositorio fuera del sandbox. Aun así se exige forma canónica para que la misma
    ubicación tenga un único fingerprint y no se pueda presentar `../../etc/passwd`
    como ubicación de un finding (AM-602).
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidSourcePathError("path", "must be a string")
        reason = _violation(self.value)
        if reason is not None:
            raise InvalidSourcePathError("path", reason)

    def __str__(self) -> str:
        return self.value
