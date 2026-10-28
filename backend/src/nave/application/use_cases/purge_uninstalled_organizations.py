"""Purga de organizaciones desinstaladas hace más de 30 días (RR-11, ADR 0014).

El caso de uso pide candidatas y la base de datos decide: la función SQL vuelve a exigir
el plazo, de modo que un fallo aquí no puede borrar un inquilino activo. Cada borrado
queda en el audit log (con `nave_app`) antes y después; si se interrumpe a medias, la
ejecución siguiente continúa donde quedó."""

import logging
from datetime import timedelta
from typing import Final

from nave.application.dto.audit import AuditEntry
from nave.application.dto.maintenance import OrganizationPurgeReport, PurgeCandidate
from nave.application.errors import PurgeRefusedError
from nave.application.ports.maintenance import OrganizationPurgeStore
from nave.application.ports.persistence import UnitOfWorkFactory
from nave.application.ports.system import Clock

_LOG: Final = logging.getLogger(__name__)
# Debe coincidir con la función SQL `nave_purge_organization`; si esta fuera menor, la
# base de datos se negaría; si fuera mayor, la aplicación esperaría de más.
PURGE_GRACE: Final = timedelta(days=30)
MAX_ORGANIZATIONS_PER_RUN: Final = 20
MAX_BATCHES_PER_ORGANIZATION: Final = 200


class PurgeUninstalledOrganizations:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, store: OrganizationPurgeStore, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._store = store
        self._clock = clock

    def execute(self) -> OrganizationPurgeReport:
        cutoff = self._clock.now() - PURGE_GRACE
        purged = in_progress = refused = 0
        for candidate in self._store.candidates(cutoff, MAX_ORGANIZATIONS_PER_RUN):
            self._audit(candidate, "organization.purge_requested", {})
            try:
                deleted, done = self._drain(candidate)
            except PurgeRefusedError:
                _LOG.error("organization purge refused", extra=_fields(candidate))
                self._audit(candidate, "organization.purge_refused", {})
                refused += 1
                continue
            if done:
                self._audit(candidate, "organization.purged", {"rows": str(deleted)})
                purged += 1
            else:
                in_progress += 1
        return OrganizationPurgeReport(purged=purged, in_progress=in_progress, refused=refused)

    def _drain(self, candidate: PurgeCandidate) -> tuple[int, bool]:
        total = 0
        for _ in range(MAX_BATCHES_PER_ORGANIZATION):
            progress = self._store.purge_batch(candidate.organization_id)
            total += progress.deleted
            if progress.done:
                return total, True
        return total, False

    def _audit(self, candidate: PurgeCandidate, action: str, details: dict[str, str]) -> None:
        with self._uow_factory() as uow:
            uow.audit.append(
                AuditEntry(
                    occurred_at=self._clock.now(),
                    action=action,
                    actor=None,
                    organization_id=candidate.organization_id,
                    target_type="organization",
                    target_id=str(candidate.organization_id.value),
                    details=details,
                )
            )
            uow.commit()


def _fields(candidate: PurgeCandidate) -> dict[str, str]:
    return {"organization_id": str(candidate.organization_id.value)}
