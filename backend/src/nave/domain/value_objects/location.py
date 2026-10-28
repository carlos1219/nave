from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidSourcePathError
from nave.domain.validation import is_int_in_range
from nave.domain.value_objects.source_path import SourcePath

MAX_LINE: Final = 10_000_000


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Fichero y, si se conoce, rango de líneas. Un hallazgo de dependencia apunta al
    manifiesto sin línea: inventar "línea 1" sería engañoso."""

    path: SourcePath
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, SourcePath):
            raise InvalidSourcePathError("path", "must be a SourcePath")
        if self.start_line is None and self.end_line is None:
            return
        if self.start_line is None or not is_int_in_range(self.start_line, 1, MAX_LINE):
            raise InvalidSourcePathError("start_line", f"must be in [1, {MAX_LINE}]")
        if self.end_line is None or not is_int_in_range(self.end_line, self.start_line, MAX_LINE):
            raise InvalidSourcePathError("end_line", "must be in [start_line, MAX_LINE]")
