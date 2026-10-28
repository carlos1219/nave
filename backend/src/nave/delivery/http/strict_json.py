"""JSON estricto para cuerpos que vienen de la red.

Rechaza claves duplicadas (dos analizadores pueden quedarse con valores distintos),
`NaN`/`Infinity`, profundidad excesiva y cualquier cosa que no sea UTF-8 válido."""

import json
from typing import Any, Final

MAX_DEPTH: Final = 32


class StrictJsonError(ValueError):
    """El cuerpo no es JSON estricto. Sin detalles: no se hace eco de la entrada."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_name: str) -> Any:
    raise StrictJsonError("non-finite number")


def _depth(value: Any, level: int = 0) -> None:
    if level > MAX_DEPTH:
        raise StrictJsonError("too deep")
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def loads_strict(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
        document = json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_constant)
        _depth(document)
    except StrictJsonError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise StrictJsonError("invalid json") from error
    return document
