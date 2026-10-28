"""Descarga de tablas de hallazgos (ADR 0029)."""

from dataclasses import dataclass
from enum import Enum
from typing import Self

from nave.domain.value_objects.enum_parsing import parse_enum


class ExportView(Enum):
    TRIAGE = "triage"
    DEPENDENCIES = "dependencies"
    ACCEPTED_RISKS = "accepted-risks"
    REPOSITORY = "repository"

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "view")


class ExportFormat(Enum):
    CSV = "csv"
    SARIF = "sarif"

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "format")


@dataclass(frozen=True, slots=True)
class ExportFile:
    content: bytes
    media_type: str
    extension: str
    rows: int
    """Filas exportadas (en SARIF, resultados con ubicación)."""
