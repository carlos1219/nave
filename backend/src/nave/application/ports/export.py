from collections.abc import Sequence
from typing import Protocol

from nave.application.dto.export import ExportFile, ExportFormat
from nave.application.dto.insights import FindingRow


class FindingExportPort(Protocol):
    def render(self, rows: Sequence[FindingRow], fmt: ExportFormat) -> ExportFile:
        """Sin E/S. El texto de los findings es no confiable: el CSV neutraliza fórmulas y
        el SARIF lo emite como code span (ADR 0029)."""
        ...
