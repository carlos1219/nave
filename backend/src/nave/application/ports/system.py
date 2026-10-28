from datetime import datetime
from typing import Protocol
from uuid import UUID


class Clock(Protocol):
    def now(self) -> datetime:
        """Instante actual, siempre con zona horaria UTC."""
        ...


class IdGenerator(Protocol):
    def new_uuid(self) -> UUID:
        """UUID aleatorio nuevo (v4). Nunca reutiliza valores."""
        ...
