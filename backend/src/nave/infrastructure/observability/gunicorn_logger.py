"""Registro de acceso de gunicorn seguro para líneas (AM-306).

La ruta de la petición llega decodificada: `%0a` se convierte en un salto de línea real y
gunicorn lo escribiría tal cual, permitiendo forjar líneas falsas en el log de la API. Aquí
se escapan los caracteres de control, las comillas y la barra invertida, y se redactan
patrones de secreto, igual que hace el formateador JSON con el resto de registros.
"""

from typing import Any, Final

from gunicorn.glogging import Logger

from nave.infrastructure.observability.logging import redact

_ESCAPED: Final = frozenset('"\\')
_FIRST_PRINTABLE: Final = 0x20
_DEL: Final = 0x7F
_LATIN1_END: Final = 0x100


def sanitize_log_value(value: str) -> str:
    out: list[str] = []
    for char in redact(value):
        code = ord(char)
        if char in _ESCAPED or code < _FIRST_PRINTABLE or code >= _DEL:
            out.append(f"\\x{code:02x}" if code < _LATIN1_END else f"\\u{code:04x}")
        else:
            out.append(char)
    return "".join(out)


class AccessLogger(Logger):  # type: ignore[misc]
    def atoms(self, resp: Any, req: Any, environ: Any, request_time: Any) -> dict[str, Any]:
        atoms: dict[str, Any] = super().atoms(resp, req, environ, request_time)
        return {
            key: sanitize_log_value(value) if isinstance(value, str) else value
            for key, value in atoms.items()
        }
