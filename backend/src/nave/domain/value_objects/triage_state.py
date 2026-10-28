from enum import Enum
from typing import Self

from nave.domain.value_objects.enum_parsing import parse_enum


class TriageState(Enum):
    OPEN = "OPEN"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    ACCEPTED_RISK = "ACCEPTED_RISK"
    FIXED = "FIXED"

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "triage_state")
