from dataclasses import dataclass

from nave.domain.errors import FindingInvariantError
from nave.domain.value_objects.finding_text import FindingMessage, Snippet
from nave.domain.value_objects.identifiers import FindingId, ScanId
from nave.domain.value_objects.location import SourceLocation


@dataclass(frozen=True, slots=True, kw_only=True)
class FindingOccurrence:
    """Dónde apareció un finding en un escaneo concreto (una por finding y escaneo).

    `is_new` se fija al procesar: el fingerprint no estaba en el baseline. Es lo que
    permite reportar en un PR solo lo que ese PR introduce (CLAUDE.md §7).
    """

    scan_id: ScanId
    finding_id: FindingId
    location: SourceLocation | None
    message: FindingMessage
    snippet: Snippet | None
    is_new: bool

    def __post_init__(self) -> None:
        if not isinstance(self.is_new, bool):
            raise FindingInvariantError("is_new must be a boolean")
