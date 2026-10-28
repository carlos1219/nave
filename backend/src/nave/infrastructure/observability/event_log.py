import logging
import re
from collections.abc import Sequence
from typing import Final

from nave.application.dto.maintenance import OutboxMessage

_LOG: Final = logging.getLogger("nave.events")
_UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.ASCII)
_TOKEN: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}", re.ASCII)


def _clean(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


class LogEventSink:
    """Consumidor del outbox que deja cada evento de dominio como una línea de log JSON.

    Es total y sin estado: nunca lanza por un evento raro (solo copia valores que cumplen un
    formato estricto) y repetir un evento solo repite una línea.
    """

    def deliver(self, messages: Sequence[OutboxMessage]) -> None:
        for message in messages:
            _LOG.info(
                "domain event",
                extra={
                    "event_type": _clean(message.type, _TOKEN),
                    "scan_id": _clean(message.payload.get("scan_id"), _UUID),
                    "status": _clean(message.payload.get("current"), _TOKEN),
                },
            )
