import unicodedata
from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidGitRefError
from nave.domain.value_objects.text import is_safe_char

_MAX_UTF8_BYTES: Final = 255
_FORBIDDEN_CHARS: Final = frozenset(" ~^:?*[\\\x7f")
_FORBIDDEN_SUBSTRINGS: Final = ("..", "//", "@{")


def _violation(value: str) -> str | None:  # noqa: PLR0911 - una regla por retorno, más legible que anidar
    if not value:
        return "must not be empty"
    # Cada carácter ocupa al menos un byte: si hay más caracteres que el límite de bytes
    # se rechaza ya, sin recorrer una entrada potencialmente enorme.
    if len(value) > _MAX_UTF8_BYTES:
        return f"must not exceed {_MAX_UTF8_BYTES} UTF-8 bytes"
    # Se valida carácter a carácter antes de codificar: un sustituto suelto haría fallar encode().
    for char in value:
        if not is_safe_char(char) or char in _FORBIDDEN_CHARS:
            return "contains a forbidden character"
        # Espacios Unicode (NBSP, etc.): confundibles con el espacio, que git prohíbe.
        if unicodedata.category(char) == "Zs":
            return "contains a forbidden character"
    if len(value.encode("utf-8")) > _MAX_UTF8_BYTES:
        return f"must not exceed {_MAX_UTF8_BYTES} UTF-8 bytes"
    # Un "-" inicial se interpretaría como opción si la ref llegara a la línea de órdenes de git.
    if value.startswith("-"):
        return "must not start with '-'"
    if value.startswith("/") or value.endswith("/"):
        return "must not start or end with '/'"
    if value.endswith(".") or value == "@":
        return "has a forbidden ending"
    if any(token in value for token in _FORBIDDEN_SUBSTRINGS):
        return "contains a forbidden sequence"
    if any(part.startswith(".") or part.endswith(".lock") for part in value.split("/")):
        return "has a component starting with '.' or ending with '.lock'"
    return None


@dataclass(frozen=True, slots=True)
class GitRef:
    """Nombre de ref de git (rama, tag o ref completa como `refs/heads/main`).

    Aplica las reglas de `git check-ref-format` y además rechaza caracteres de control
    y de formato Unicode (bidi, ancho cero), espacios Unicode y el `-` inicial. Se
    admite UTF-8: rechazar ramas no ASCII dejaría sin escanear código legítimo.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidGitRefError("ref", "must be a string")
        reason = _violation(self.value)
        if reason is not None:
            raise InvalidGitRefError("ref", reason)

    def __str__(self) -> str:
        return self.value
