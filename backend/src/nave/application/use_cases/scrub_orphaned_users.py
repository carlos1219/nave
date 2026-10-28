"""Minimización de datos personales de usuarios sin acceso a ninguna organización.

Cuando se purga una organización (RR-11) o se retira a alguien de todas, su nombre y correo
dejan de tener finalidad. Se vacían tras un plazo de gracia; la fila se conserva porque el
audit log y las decisiones de triage la referencian. El siguiente login los vuelve a
rellenar. La base de datos repite el plazo y las condiciones: este caso de uso no puede
vaciar datos de quien aún tiene acceso o sesión."""

import logging
from datetime import timedelta
from typing import Final

from nave.application.ports.maintenance import OrphanedUserStore
from nave.application.ports.system import Clock

_LOG: Final = logging.getLogger(__name__)
# Debe coincidir con la función SQL `nave_scrub_orphan_users`.
SCRUB_GRACE: Final = timedelta(days=30)
BATCH_SIZE: Final = 1000
MAX_BATCHES: Final = 50


class ScrubOrphanedUsers:
    def __init__(self, *, store: OrphanedUserStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def execute(self) -> int:
        cutoff = self._clock.now() - SCRUB_GRACE
        total = 0
        for _ in range(MAX_BATCHES):
            scrubbed = self._store.scrub_batch(cutoff, BATCH_SIZE)
            total += scrubbed
            if scrubbed < BATCH_SIZE:
                break
        if total:
            _LOG.info("orphaned users scrubbed", extra={"count": total})
        return total
