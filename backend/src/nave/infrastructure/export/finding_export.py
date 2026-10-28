"""Exportación de filas de hallazgos a CSV o SARIF 2.1.0 (puerto `FindingExportPort`, ADR 0029).

Los títulos, reglas, rutas y justificaciones vienen de repositorios no confiables o de usuarios.
En CSV, una celda que empieza por `=`, `+`, `-`, `@`, tabulador o retorno de carro la ejecuta
como fórmula una hoja de cálculo (inyección de fórmulas): se prefija con `'`. Los caracteres de
control se eliminan. El CSV no incluye mensajes ni fragmentos de código."""

import csv
import io
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from nave.application.dto.export import ExportFile, ExportFormat
from nave.application.dto.insights import FindingRow
from nave.application.dto.views import FindingView
from nave.infrastructure.vcs.sarif_writer import sarif_document

_FORMULA_START: Final = frozenset("=+-@\t\r")
MAX_CELL: Final = 2000
HEADER: Final = (
    "severity",
    "triage_state",
    "scanner",
    "category",
    "rule_id",
    "title",
    "cve",
    "cwe",
    "repository",
    "path",
    "line",
    "first_seen_at",
    "last_seen_at",
    "triage_expires_at",
    "justification",
)


def safe_cell(value: str) -> str:
    text = "".join(c for c in value if c.isprintable() or c == " ")[:MAX_CELL]
    if text[:1] in _FORMULA_START or text.lstrip()[:1] in _FORMULA_START:
        return "'" + text
    return text


def _time(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _record(row: FindingRow) -> list[str]:
    f = row.finding
    location = row.occurrence.location if row.occurrence else None
    cells = [
        f.severity.value,
        f.triage_state.value,
        f.scanner_kind.value,
        f.category.value,
        f.rule_id.value,
        f.title.value,
        " ".join(c.value for c in f.cve),
        " ".join(c.value for c in f.cwe),
        f"{row.repository.owner}/{row.repository.name}",
        location.path.value if location else "",
        str(location.start_line) if location and location.start_line is not None else "",
        _time(f.first_seen_at),
        _time(f.last_seen_at),
        _time(f.triage_expires_at),
        row.justification or "",
    ]
    return [safe_cell(c) for c in cells]


class FindingExporter:
    def render(self, rows: Sequence[FindingRow], fmt: ExportFormat) -> ExportFile:
        if fmt is ExportFormat.SARIF:
            content, count = sarif_document([FindingView(r.finding, r.occurrence) for r in rows])
            return ExportFile(content, "application/sarif+json", "sarif", count)
        buffer = io.StringIO()
        writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writerow(HEADER)
        writer.writerows(_record(r) for r in rows)
        # BOM: sin él, las hojas de cálculo abren el UTF-8 como Latin-1 y rompen los acentos.
        content = ("﻿" + buffer.getvalue()).encode("utf-8")
        return ExportFile(content, "text/csv", "csv", len(rows))
