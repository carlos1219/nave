import re
from dataclasses import dataclass
from typing import Final, Self
from uuid import UUID

from nave.domain.errors import InvalidIdentifierError
from nave.domain.validation import fullmatch_str

_CANONICAL_UUID: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.ASCII
)


@dataclass(frozen=True, slots=True)
class EntityId:
    """Base de los identificadores. Cada subtipo es incomparable con los demás."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise InvalidIdentifierError(type(self).__name__, "must be a UUID")

    @classmethod
    def parse(cls, raw: str) -> Self:
        # UUID() también acepta llaves, prefijo urn:, mayúsculas y la forma sin guiones.
        # Solo se admite la forma canónica para que un recurso tenga una única
        # representación textual (claves de caché, auditoría, comparación de logs).
        if not fullmatch_str(_CANONICAL_UUID, raw):
            raise InvalidIdentifierError(cls.__name__, "must be a canonical lowercase UUID")
        return cls(UUID(raw))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ScanId(EntityId):
    """Identificador de un escaneo."""


@dataclass(frozen=True, slots=True)
class FindingId(EntityId):
    """Identificador de un finding dentro de un repositorio."""


@dataclass(frozen=True, slots=True)
class RepositoryId(EntityId):
    """Identificador interno de un repositorio (no el ID numérico de GitHub)."""


@dataclass(frozen=True, slots=True)
class OrganizationId(EntityId):
    """Identificador interno de una organización (una instalación de la GitHub App)."""


@dataclass(frozen=True, slots=True)
class UserId(EntityId):
    """Identificador interno de un usuario."""


@dataclass(frozen=True, slots=True)
class TriageDecisionId(EntityId):
    """Identificador de una decisión de triage (registro inmutable)."""
