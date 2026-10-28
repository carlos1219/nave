from datetime import datetime, timedelta

from nave.domain.errors import InvalidTimestampError

_UTC_OFFSET = timedelta(0)


def ensure_utc(value: datetime, field: str) -> datetime:
    """Exige un `datetime` con zona horaria UTC.

    No convierte: una hora local o sin zona indica un `Clock` mal configurado,
    y convertir en silencio ocultaría el fallo.
    """
    if not isinstance(value, datetime):
        raise InvalidTimestampError(field, "must be a datetime")
    offset = value.utcoffset()
    if offset is None:
        raise InvalidTimestampError(field, "must be timezone-aware")
    if offset != _UTC_OFFSET:
        raise InvalidTimestampError(field, "must be expressed in UTC")
    return value
