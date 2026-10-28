"""Consultas de lectura agregadas por organización. Las implementa `infrastructure.persistence`.

Ninguna autoriza: lo hace el caso de uso antes de llamarlas. Todas filtran por la
organización recibida y devuelven listas acotadas."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from nave.application.dto.export import ExportView
from nave.application.dto.insights import (
    AuditRecord,
    FailedScan,
    FindingFilters,
    FindingRow,
    FixTime,
    OpenBucket,
    PolicyVersion,
    QueueCursor,
    RuleStat,
    ScanActivity,
    SummaryPoint,
)
from nave.domain.value_objects.identifiers import OrganizationId
from nave.domain.value_objects.severity import Severity


class InsightsQueries(Protocol):
    def open_buckets(self, organization_id: OrganizationId) -> Sequence[OpenBucket]:
        """Findings `OPEN` agrupados por repositorio, escáner y severidad."""
        ...

    def rule_stats(self, organization_id: OrganizationId, *, limit: int) -> Sequence[RuleStat]:
        """Reglas que han producido findings: más abiertos primero."""
        ...

    def fix_times(self, organization_id: OrganizationId) -> Sequence[FixTime]: ...

    def overdue_counts(
        self, organization_id: OrganizationId, cutoffs: Mapping[Severity, datetime]
    ) -> Mapping[Severity, int]:
        """Findings `OPEN` de cada severidad detectados por primera vez antes de su corte."""
        ...

    def open_summaries(
        self, organization_id: OrganizationId, *, since: datetime
    ) -> Sequence[SummaryPoint]:
        """Escaneos completados de la rama por defecto desde `since`, más el último anterior
        de cada repositorio (punto de partida), ordenados por `finished_at`."""
        ...

    def recent_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        """Escaneos en curso y los más recientes, el más reciente primero."""
        ...

    def accepted_risks(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[FindingRow]:
        """Findings `ACCEPTED_RISK`, el que caduca antes primero, con su justificación."""
        ...

    def dependency_findings(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[FindingRow]:
        """Findings de dependencias (SCA), abiertos primero y de mayor severidad."""
        ...

    def policy_versions(self, organization_id: OrganizationId) -> Sequence[PolicyVersion]:
        """Todas las versiones de la política, la más reciente primero."""
        ...

    def audit_entries(
        self, organization_id: OrganizationId, *, limit: int, before: int | None
    ) -> Sequence[AuditRecord]:
        """Entradas de la organización, la más reciente primero, con id menor que `before`."""
        ...

    def pull_request_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        """El escaneo más reciente de cada PR (repositorio y número), el más reciente primero."""
        ...

    def triage_queue(
        self,
        organization_id: OrganizationId,
        filters: FindingFilters,
        *,
        after: QueueCursor | None,
        limit: int,
    ) -> Sequence[FindingRow]:
        """Findings `OPEN` por severidad descendente, `first_seen_at` e id ascendentes,
        estrictamente después de `after`, con la ocurrencia del último escaneo que los vio.
        `filters.states` se ignora. Un `repository_id` de otra organización no devuelve nada."""
        ...

    def failed_scans(self, organization_id: OrganizationId, *, limit: int) -> Sequence[FailedScan]:
        """`FAILED` y `TIMED_OUT`, el más reciente primero."""
        ...

    def completed_scans(
        self, organization_id: OrganizationId, *, limit: int
    ) -> Sequence[ScanActivity]:
        """`COMPLETED` con resumen, el que terminó más tarde primero."""
        ...

    def export_rows(
        self,
        organization_id: OrganizationId,
        view: ExportView,
        filters: FindingFilters,
        *,
        limit: int,
    ) -> Sequence[FindingRow]:
        """Filas de una vista para descargar, con ocurrencia. `REPOSITORY` exige
        `filters.repository_id` (de esa organización); el resto lo usa como filtro."""
        ...
