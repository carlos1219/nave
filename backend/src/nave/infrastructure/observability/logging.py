"""Logs JSON estructurados (CLAUDE.md §16) con redacción de secretos (AM-306).

Principios: un registro es una línea JSON (no hay inyección de líneas falsas, porque los
saltos de línea se escapan); solo salen los campos de una lista cerrada, así un
`extra=` descuidado no filtra un token; de una excepción se registran el tipo y los
frames, nunca su mensaje ni sus variables locales, que pueden contener secretos o código
escaneado.
"""

import json
import logging
import re
import sys
import traceback
from datetime import UTC, datetime
from typing import Any, Final

ALLOWED_FIELDS: Final = frozenset(
    {
        "scan_id",
        "run_id",
        "request_id",
        "delivery_id",
        "task",
        "task_id",
        "retries",
        "outcome",
        "delay_s",
        "error",
        "queue",
        "count",
        "duration_s",
        "event_type",
        "status",
        "action",
        "user_id",
        "organization_id",
        "target_type",
        "target_id",
    }
)
_MAX_MESSAGE: Final = 2000
_REDACTED: Final = "[REDACTED]"
_SECRET_PATTERNS: Final = (
    re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?:gh[pousr]_[A-Za-z0-9._-]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*\S+"),
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]*)?"),
    re.compile(r"(?i)\b[a-z_-]*session[a-z_-]*\s*=\s*[A-Za-z0-9_-]{16,}"),
)


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            # Se redacta antes del recorte final: cortar primero dejaría pasar el principio de
            # un secreto que cruza el límite. El margen acota el coste de las expresiones.
            "message": redact(record.getMessage()[: _MAX_MESSAGE * 4])[:_MAX_MESSAGE],
        }
        for name in ALLOWED_FIELDS:
            value = record.__dict__.get(name)
            if value is None:
                continue
            entry[name] = value if isinstance(value, int | float | bool) else redact(str(value))
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
            frames = traceback.extract_tb(record.exc_info[2])
            entry["stack"] = [f"{f.filename}:{f.lineno}:{f.name}" for f in frames[-12:]]
        return json.dumps(entry, ensure_ascii=True, separators=(",", ":"), default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
