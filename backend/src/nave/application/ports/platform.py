"""Puertos de administración de plataforma (ADR 0030). Ninguno autoriza: lo hace el caso de
uso con `PlatformAuthorizer` antes de llamarlos."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from nave.application.dto.platform import (
    ComponentHealth,
    CounterBucket,
    DeliveryRecord,
    PlatformOrganization,
    PlatformUser,
    WebhookReason,
)
from nave.domain.value_objects.identifiers import UserId


class PlatformAdminRepository(Protocol):
    def is_admin(self, user: UserId) -> bool: ...

    def list_admins(self) -> Sequence[UserId]: ...

    def grant(self, user: UserId, at: datetime) -> bool:
        """`True` si no lo era. Solo lo usa la CLI."""
        ...

    def revoke(self, user: UserId) -> bool:
        """`True` si lo era. Solo lo usa la CLI."""
        ...


class PlatformQueries(Protocol):
    def users(self, *, limit: int, now: datetime) -> Sequence[PlatformUser]:
        """Los de actividad más reciente primero, con organizaciones no desinstaladas y
        sesiones no caducadas a `now`."""
        ...

    def organizations(self) -> Sequence[PlatformOrganization]:
        """Organizaciones no desinstaladas, por login (hasta 1000)."""
        ...

    def recent_deliveries(self, *, limit: int) -> Sequence[DeliveryRecord]: ...

    def delivery_outcomes(self, *, since: datetime) -> Mapping[str, int]: ...

    def webhook_counters(self, *, since: datetime) -> Sequence[CounterBucket]:
        """Por minuto ascendente."""
        ...

    def health_reports(self) -> Sequence[ComponentHealth]: ...


class HealthStore(Protocol):
    def save(self, reports: Sequence[ComponentHealth]) -> None:
        """Sustituye el último resultado de cada componente."""
        ...


class HealthProbesPort(Protocol):
    def probe(self) -> Sequence[ComponentHealth]:
        """Comprueba los componentes que ve este proceso. Nunca lanza: un fallo es un DOWN."""
        ...


class WebhookStatsPort(Protocol):
    def count(self, reason: WebhookReason) -> None:
        """Anota un rechazo o duplicado. Nunca lanza y no escribe en cada llamada: el
        adaptador acumula y vuelca con un intervalo mínimo (ADR 0030)."""
        ...


class WebhookCounterStore(Protocol):
    def add(self, counts: Mapping[tuple[datetime, WebhookReason], int]) -> None:
        """Suma a los contadores de cada `(minuto, motivo)`."""
        ...
