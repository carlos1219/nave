from collections.abc import Sequence
from typing import Protocol

from nave.application.dto.vcs import PrCommentBody, VcsTarget
from nave.application.dto.views import FindingView
from nave.domain.entities.scan import Scan


class VcsReporterPort(Protocol):
    """Reporte a GitHub. Todas las operaciones son idempotentes: repetirlas no
    duplica checks ni comentarios."""

    def publish_check(self, target: VcsTarget, scan: Scan) -> None:
        """Crea o actualiza el check run del commit según el estado final del escaneo."""
        ...

    def upload_sarif(self, target: VcsTarget, scan: Scan, findings: Sequence[FindingView]) -> None:
        """Sube a Code Scanning los findings presentes en el escaneo (secretos redactados)."""
        ...

    def upsert_pr_comment(self, target: VcsTarget, scan: Scan, body: PrCommentBody) -> None:
        """Crea o reemplaza el comentario de NAVE en el PR (identificado por su marcador)."""
        ...
