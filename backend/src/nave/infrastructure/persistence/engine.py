from datetime import timedelta
from typing import Final

from sqlalchemy import URL, Engine, create_engine

_DRIVER: Final = "postgresql+psycopg"


def create_database_engine(
    url: URL,
    *,
    statement_timeout: timedelta = timedelta(seconds=15),
    pool_size: int = 5,
) -> Engine:
    """Motor de PostgreSQL endurecido.

    - `timezone=UTC`: las fechas vuelven en UTC, que es lo que exige el dominio.
    - `statement_timeout`: una consulta patológica no bloquea un worker indefinidamente.
    - `idle_in_transaction_session_timeout`: una transacción olvidada no retiene locks.
    - La contraseña viaja en el objeto `URL` (construido desde un Docker secret), nunca
      en una cadena que pueda acabar en un log.
    """
    if url.drivername != _DRIVER:
        raise ValueError(f"only {_DRIVER} is supported")
    timeout_ms = int(statement_timeout.total_seconds() * 1000)
    if timeout_ms <= 0:
        raise ValueError("statement_timeout must be positive")
    options = (
        f"-c timezone=UTC -c statement_timeout={timeout_ms} "
        f"-c idle_in_transaction_session_timeout={timeout_ms * 4}"
    )
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=pool_size,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"options": options, "application_name": "nave"},
    )
