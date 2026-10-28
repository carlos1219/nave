from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.quality_gates import Verdict
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.tool_run import ToolRun

MAX_TOOLS: Final = 16


@dataclass(frozen=True, slots=True)
class ScanSummary:
    """Resultado agregado de un escaneo completado."""

    new: SeverityCounts
    open: SeverityCounts
    verdict: Verdict
    truncated: bool
    tools: tuple[ToolRun, ...] = ()
    """Vacío en escaneos anteriores al ADR 0029."""

    def __post_init__(self) -> None:
        if not isinstance(self.new, SeverityCounts):
            raise InvalidValueError("summary.new", "must be SeverityCounts")
        if not isinstance(self.open, SeverityCounts):
            raise InvalidValueError("summary.open", "must be SeverityCounts")
        if not isinstance(self.verdict, Verdict):
            raise InvalidValueError("summary.verdict", "must be a Verdict")
        if not isinstance(self.truncated, bool):
            raise InvalidValueError("summary.truncated", "must be a boolean")
        if len(self.tools) > MAX_TOOLS or not all(isinstance(t, ToolRun) for t in self.tools):
            raise InvalidValueError("summary.tools", f"must be a tuple of at most {MAX_TOOLS} runs")
