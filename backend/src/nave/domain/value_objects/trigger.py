import re
from dataclasses import dataclass
from enum import Enum
from typing import Final, Self

from nave.domain.errors import InvalidTriggerKeyError
from nave.domain.validation import fullmatch_str
from nave.domain.value_objects.enum_parsing import parse_enum

_KEY_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", re.ASCII)


class ScanTrigger(Enum):
    WEBHOOK = "WEBHOOK"
    MANUAL = "MANUAL"

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "trigger")


@dataclass(frozen=True, slots=True)
class TriggerKey:
    """Clave de idempotencia del disparador.

    Ejemplos: `gh-delivery:<X-GitHub-Delivery>` o `manual:<id de petición>`. Dos
    peticiones con la misma clave designan el mismo escaneo.
    """

    value: str

    def __post_init__(self) -> None:
        if not fullmatch_str(_KEY_PATTERN, self.value):
            raise InvalidTriggerKeyError(
                "trigger_key", "must be 1-128 characters from [A-Za-z0-9:._-]"
            )

    def __str__(self) -> str:
        return self.value
