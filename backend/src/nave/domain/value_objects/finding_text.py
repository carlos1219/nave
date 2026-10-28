"""Texto de findings: lo escribe el atacante (AT-2) y lo leen personas."""

from dataclasses import dataclass
from typing import ClassVar, Final, Self

from nave.domain.errors import InvalidFindingTextError
from nave.domain.value_objects.text import is_safe_text, reveal_unsafe

MULTILINE: Final = frozenset("\n\t")
_EMPTY_PLACEHOLDER: Final = "-"


@dataclass(frozen=True, slots=True)
class BoundedText:
    """Texto acotado. Los subtipos fijan límites y los caracteres de control permitidos.

    El constructor es estricto (rechaza). `from_untrusted` es la vía para texto que
    viene de escáneres: hace visibles los caracteres inseguros y trunca.
    """

    value: str

    field_name: ClassVar[str] = "text"
    min_length: ClassVar[int] = 1
    max_length: ClassVar[int] = 1
    allowed_controls: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidFindingTextError(self.field_name, "must be a string")
        if not self.min_length <= len(self.value) <= self.max_length:
            raise InvalidFindingTextError(
                self.field_name, f"length must be in [{self.min_length}, {self.max_length}]"
            )
        if not is_safe_text(self.value, self.allowed_controls):
            raise InvalidFindingTextError(self.field_name, "contains forbidden characters")

    @classmethod
    def from_untrusted(cls, text: str) -> Self:
        revealed = reveal_unsafe(text, cls.max_length, cls.allowed_controls)
        return cls(revealed or _EMPTY_PLACEHOLDER)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FindingTitle(BoundedText):
    field_name = "title"
    max_length = 500


@dataclass(frozen=True, slots=True)
class FindingMessage(BoundedText):
    field_name = "message"
    max_length = 4096
    allowed_controls = MULTILINE


@dataclass(frozen=True, slots=True)
class Snippet(BoundedText):
    field_name = "snippet"
    max_length = 2048
    allowed_controls = MULTILINE


@dataclass(frozen=True, slots=True)
class Justification(BoundedText):
    """Motivo humano de una decisión de triage. Mínimo 10 caracteres: obliga a explicar."""

    field_name = "justification"
    min_length = 10
    max_length = 2000
    allowed_controls = MULTILINE
