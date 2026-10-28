"""Constructores de `FindingView` para tests de exportación y de reporter."""

from datetime import UTC, datetime
from uuid import UUID

from nave.application.dto.views import FindingView
from nave.domain.entities.finding import Finding
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import FindingMessage, FindingTitle
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.identifiers import FindingId, RepositoryId, ScanId
from nave.domain.value_objects.location import SourceLocation
from nave.domain.value_objects.occurrence import FindingOccurrence
from nave.domain.value_objects.rule_id import RuleId
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.source_path import SourcePath

REPO = RepositoryId(UUID("00000000-0000-4000-8000-00000000a001"))
SCAN = ScanId(UUID("00000000-0000-4000-8000-0000000000e1"))


def make_view(  # noqa: PLR0913 - constructor de datos de prueba
    n: int = 1,
    *,
    title: str = "Titulo",
    message: str = "mensaje",
    rule: str = "rule.x",
    path: str | None = "src/a.py",
    line: int | None = 3,
    severity: Severity = Severity.HIGH,
    category: FindingCategory = FindingCategory.SAST,
    kind: str = "semgrep",
) -> FindingView:
    finding = Finding.discover(
        finding_id=FindingId(UUID(int=n)),
        repository_id=REPO,
        fingerprint=Fingerprint(f"{n:064x}"),
        scanner_kind=ScannerKind(kind),
        category=category,
        rule_id=RuleId(rule),
        severity=severity,
        title=FindingTitle(title),
        scan_id=SCAN,
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    location = None
    if path is not None:
        location = (
            SourceLocation(SourcePath(path), line, line)
            if line
            else SourceLocation(SourcePath(path))
        )
    occurrence = FindingOccurrence(
        scan_id=SCAN,
        finding_id=finding.id,
        location=location,
        message=FindingMessage(message),
        snippet=None,
        is_new=True,
    )
    return FindingView(finding, occurrence)
