from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Self

from nave.domain.errors import InvalidValueError
from nave.domain.validation import is_int_in_range
from nave.domain.value_objects.severity import Severity

MAX_COUNT: Final = 2**31 - 1


@dataclass(frozen=True, slots=True)
class SeverityCounts:
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0

    def __post_init__(self) -> None:
        for severity in Severity:
            if not is_int_in_range(self.get(severity), 0, MAX_COUNT):
                raise InvalidValueError("severity_counts", "counts must be non-negative integers")

    @classmethod
    def of(cls, severities: Iterable[Severity]) -> Self:
        tally = dict.fromkeys(Severity, 0)
        for severity in severities:
            tally[severity] += 1
        return cls(**{severity.value.lower(): count for severity, count in tally.items()})

    def get(self, severity: Severity) -> int:
        count: int = getattr(self, severity.value.lower())
        return count

    @property
    def total(self) -> int:
        return sum(self.get(severity) for severity in Severity)
