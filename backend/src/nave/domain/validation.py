"""Comprobaciones de tipo en tiempo de ejecución para valores de frontera.

Los value objects se construyen con datos de JSON, de la base de datos y de la
salida de escáneres, donde los tipos estáticos no están garantizados. Estas
funciones aceptan `object` a propósito.
"""

import re


def fullmatch_str(pattern: re.Pattern[str], value: object) -> bool:
    """`value` es `str` y coincide completo con `pattern`.

    Siempre `fullmatch`: con `match` + `$` se aceptaría un salto de línea final.
    """
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def is_int_in_range(value: object, minimum: int, maximum: int) -> bool:
    """`value` es `int` (no `bool`) dentro de `[minimum, maximum]`."""
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def is_str_with_length(value: object, minimum: int, maximum: int) -> bool:
    """`value` es `str` con longitud (en caracteres) dentro de `[minimum, maximum]`."""
    return isinstance(value, str) and minimum <= len(value) <= maximum
