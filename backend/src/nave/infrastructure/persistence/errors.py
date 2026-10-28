import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from nave.application.errors import (
    ApplicationError,
    ConcurrentModificationError,
    NotFoundError,
    PersistenceUnavailableError,
)

_LOG: Final = logging.getLogger("nave.persistence")
_UNIQUE_VIOLATION: Final = "23505"
_FOREIGN_KEY_VIOLATION: Final = "23503"
_RETRYABLE: Final = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available
        "57014",  # query_canceled (statement_timeout)
        "57P01",  # admin_shutdown
    }
)


def translate_database_error(error: DBAPIError) -> ApplicationError | None:
    """Traduce un error del driver a un error de aplicación, o `None` si es un fallo de
    programación que debe propagarse tal cual (p. ej. una CHECK violada).

    Los mensajes son genéricos a propósito: el texto original contiene SQL y parámetros,
    que no deben llegar a logs ni a respuestas.
    """
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == _UNIQUE_VIOLATION:
        return ConcurrentModificationError("a conflicting row already exists")
    if sqlstate == _FOREIGN_KEY_VIOLATION:
        # Para el cliente es un 404, pero casi siempre delata un fallo de orden de escritura
        # del servidor (F7-15 se vio como «no encontrado» durante días): el nombre de la
        # restricción (sin SQL ni valores) deja rastro para diagnosticarlo.
        diag = getattr(error.orig, "diag", None)
        _LOG.warning(
            "foreign key violation translated to not-found",
            extra={"error": str(getattr(diag, "constraint_name", None) or "unknown")},
        )
        return NotFoundError("related resource")
    if (
        sqlstate in _RETRYABLE
        or error.connection_invalidated
        or isinstance(error, OperationalError | InterfaceError)
    ):
        return PersistenceUnavailableError("database temporarily unavailable")
    return None


@contextmanager
def translated_errors() -> Iterator[None]:
    """Traduce los errores del driver dentro del bloque (para los adaptadores que no
    pasan por `SqlUnitOfWork`, como los de mantenimiento)."""
    try:
        yield
    except DBAPIError as error:
        translated = translate_database_error(error)
        if translated is None:
            raise
        raise translated from error
