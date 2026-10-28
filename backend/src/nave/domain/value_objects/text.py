"""Reglas para texto procedente de fuentes no confiables."""

import unicodedata
from typing import Final

# Cc: control (incluye ESC de secuencias ANSI); Cf: formato (bidi, ancho cero: Trojan
# Source); Cs/Co/Cn: sustitutos, uso privado, sin asignar; Zl/Zp: separadores de línea.
_UNSAFE_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_REPLACEMENT: Final = "?"
_ELLIPSIS: Final = "\N{HORIZONTAL ELLIPSIS}"
_NONE: Final[frozenset[str]] = frozenset()


def is_safe_char(char: str, allowed: frozenset[str] = _NONE) -> bool:
    return char in allowed or unicodedata.category(char) not in _UNSAFE_CATEGORIES


def is_safe_text(text: str, allowed: frozenset[str] = _NONE) -> bool:
    return all(is_safe_char(char, allowed) for char in text)


def _require_positive(max_length: int) -> None:
    if max_length < 1:
        raise ValueError("max_length must be positive")


def sanitize_untrusted(text: str, max_length: int) -> str:
    """Sustituye caracteres inseguros por `?` y trunca a `max_length` caracteres.

    Se sustituye en lugar de eliminar para que la manipulación quede visible.
    """
    _require_positive(max_length)
    # Se trunca antes de sanear (la sustitución es 1:1 por carácter) para que el coste no
    # dependa del tamaño de la entrada: stderr de un escáner puede ocupar megabytes.
    truncated = len(text) > max_length
    head = text[: max_length - 1] if truncated else text
    cleaned = "".join(char if is_safe_char(char) else _REPLACEMENT for char in head)
    return cleaned + _ELLIPSIS if truncated else cleaned


def reveal_unsafe(text: str, max_length: int, allowed: frozenset[str] = _NONE) -> str:
    """Hace visibles los caracteres inseguros como `<U+202E>` y trunca (AM-703).

    A diferencia de `sanitize_untrusted`, conserva qué carácter había: es lo que necesita
    quien revisa un snippet con Trojan Source. El recorrido se detiene en cuanto la salida
    supera el límite, así que el coste no depende del tamaño de la entrada. El resultado
    nunca supera `max_length` y, si se recorta, termina en una elipsis.
    """
    _require_positive(max_length)
    parts: list[str] = []
    length = 0
    for char in text:
        piece = char if is_safe_char(char, allowed) else f"<U+{ord(char):04X}>"
        parts.append(piece)
        length += len(piece)
        if length > max_length:
            break
    revealed = "".join(parts)
    if len(revealed) <= max_length:
        return revealed
    return revealed[: max_length - 1] + _ELLIPSIS
