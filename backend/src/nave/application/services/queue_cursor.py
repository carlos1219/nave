"""Cursor opaco de la cola de triage (ADR 0029): `rango_microsegundos_uuid`."""

import re
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from nave.application.dto.insights import QueueCursor
from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str
from nave.domain.value_objects.identifiers import FindingId

_PATTERN: Final = re.compile(r"([0-9])_([0-9]{1,18})_([0-9a-f]{32})", re.ASCII)
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def encode_queue_cursor(cursor: QueueCursor) -> str:
    micros = (cursor.first_seen_at - _EPOCH) // timedelta(microseconds=1)
    return f"{cursor.rank}_{micros}_{cursor.finding_id.value.hex}"


def decode_queue_cursor(raw: str) -> QueueCursor:
    match = _PATTERN.fullmatch(raw) if fullmatch_str(_PATTERN, raw) else None
    if match is None:
        raise InvalidValueError("cursor", "is not a valid cursor")
    rank, micros, finding = match.groups()
    return QueueCursor(
        rank=int(rank),
        first_seen_at=_EPOCH + timedelta(microseconds=int(micros)),
        finding_id=FindingId(UUID(hex=finding)),
    )
