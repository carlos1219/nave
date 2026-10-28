"""Adaptadores SQL de los trabajos periódicos (ADR 0008).

`SqlOutboxStore` y `SqlScanSweepStore` usan el motor de `nave_app`. `SqlRetentionStore`
usa el motor de `nave_maintenance`, único rol con `DELETE` sobre las tablas con retención
y sin acceso a `audit_log` ni `triage_decisions`.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Engine,
    and_,
    delete,
    exists,
    func,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.exc import DBAPIError

from nave.application.dto.maintenance import (
    OutboxMessage,
    PurgeCandidate,
    PurgeProgress,
    StaleScan,
)
from nave.application.errors import PurgeRefusedError
from nave.application.ports.system import Clock
from nave.domain.value_objects.identifiers import OrganizationId, ScanId
from nave.domain.value_objects.scan_status import ScanStatus
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.errors import translated_errors

_RUNNING = (
    ScanStatus.FETCHING.value,
    ScanStatus.SCANNING.value,
    ScanStatus.PROCESSING.value,
)
_TERMINAL = (
    ScanStatus.COMPLETED.value,
    ScanStatus.FAILED.value,
    ScanStatus.CANCELLED.value,
    ScanStatus.TIMED_OUT.value,
)
_BRANCH_PREFIX = "refs/heads/"


class SqlOutboxStore:
    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def relay_batch(self, limit: int, deliver: Callable[[Sequence[OutboxMessage]], None]) -> int:
        o = t.outbox_events
        with translated_errors(), self._engine.begin() as conn:
            # SKIP LOCKED: dos relays a la vez toman lotes distintos en lugar de bloquearse.
            rows = conn.execute(
                select(o.c.id, o.c.type, o.c.payload, o.c.occurred_at)
                .where(o.c.published_at.is_(None))
                .order_by(o.c.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            if not rows:
                return 0
            deliver(
                [
                    OutboxMessage(
                        id=int(r.id), type=r.type, payload=r.payload, occurred_at=r.occurred_at
                    )
                    for r in rows
                ]
            )
            conn.execute(
                update(o)
                .where(o.c.id.in_([r.id for r in rows]))
                .values(published_at=self._clock.now())
            )
            return len(rows)


class SqlScanSweepStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def queued_before(self, cutoff: datetime, limit: int) -> Sequence[StaleScan]:
        s = t.scans
        statement = (
            select(s.c.id, s.c.status, s.c.queued_at, s.c.requeued_at)
            .where(s.c.status == ScanStatus.QUEUED.value, s.c.queued_at < cutoff)
            .order_by(s.c.queued_at, s.c.id)
            .limit(limit)
        )
        return self._stale(statement, since_column="queued_at")

    def running_before(self, cutoff: datetime, limit: int) -> Sequence[StaleScan]:
        s = t.scans
        since = func.coalesce(s.c.started_at, s.c.queued_at).label("since")
        statement = (
            select(s.c.id, s.c.status, since, s.c.requeued_at)
            .where(s.c.status.in_(_RUNNING), func.coalesce(s.c.started_at, s.c.queued_at) < cutoff)
            .order_by(since, s.c.id)
            .limit(limit)
        )
        return self._stale(statement, since_column="since")

    def mark_requeued(self, scan_id: ScanId, at: datetime) -> None:
        s = t.scans
        with translated_errors(), self._engine.begin() as conn:
            conn.execute(
                update(s)
                .where(s.c.id == scan_id.value, s.c.status == ScanStatus.QUEUED.value)
                .values(requeued_at=at)
            )

    def _stale(self, statement: object, *, since_column: str) -> Sequence[StaleScan]:
        with translated_errors(), self._engine.connect() as conn:
            rows = conn.execute(statement).all()  # type: ignore[call-overload]
        return [
            StaleScan(
                scan_id=ScanId(r.id),
                status=ScanStatus.parse(r.status),
                since=getattr(r, since_column),
                requeued_at=r.requeued_at,
            )
            for r in rows
        ]


class SqlRetentionStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def purge_webhook_deliveries(self, before: datetime, limit: int) -> int:
        w = t.webhook_deliveries
        pick = select(w.c.delivery_id).where(w.c.received_at < before).limit(limit)
        return self._delete(delete(w).where(w.c.delivery_id.in_(pick)))

    def purge_webhook_counters(self, before: datetime, limit: int) -> int:
        c = t.webhook_counters
        pick = select(c.c.minute, c.c.reason).where(c.c.minute < before).limit(limit)
        return self._delete(delete(c).where(tuple_(c.c.minute, c.c.reason).in_(pick)))

    def purge_scan_logs(self, before: datetime, limit: int) -> int:
        lg = t.scan_logs
        pick = select(lg.c.scan_id, lg.c.seq).where(lg.c.at < before).limit(limit)
        return self._delete(delete(lg).where(tuple_(lg.c.scan_id, lg.c.seq).in_(pick)))

    def purge_published_outbox(self, before: datetime, limit: int) -> int:
        o = t.outbox_events
        pick = select(o.c.id).where(o.c.published_at.is_not(None), o.c.published_at < before)
        return self._delete(delete(o).where(o.c.id.in_(pick.limit(limit))))

    def purge_scans(self, before: datetime, limit: int) -> int:
        s = t.scans
        with translated_errors(), self._engine.begin() as conn:
            ids = [row.id for row in conn.execute(self._purgeable_scans(before).limit(limit)).all()]
            if not ids:
                return 0
            conn.execute(delete(t.scan_logs).where(t.scan_logs.c.scan_id.in_(ids)))
            conn.execute(
                delete(t.policy_evaluations).where(t.policy_evaluations.c.scan_id.in_(ids))
            )
            conn.execute(
                delete(t.finding_occurrences).where(t.finding_occurrences.c.scan_id.in_(ids))
            )
            return conn.execute(delete(s).where(s.c.id.in_(ids))).rowcount

    @staticmethod
    def _purgeable_scans(before: datetime):  # type: ignore[no-untyped-def]
        s = t.scans
        newer = t.scans.alias("newer")
        superseding = t.scans.alias("superseding")
        f = t.findings

        def default_refs(scan: Any) -> tuple[Any, Any]:
            # `correlate` explícito: sin él, SQLAlchemy correlaciona el escalar dentro del
            # EXISTS contra la consulta equivocada y devuelve varias filas.
            branch = (
                select(
                    func.regexp_replace(t.repositories.c.default_branch, f"^{_BRANCH_PREFIX}", "")
                )
                .where(t.repositories.c.id == scan.c.repository_id)
                .correlate(scan)
                .scalar_subquery()
            )
            return branch, func.concat(_BRANCH_PREFIX, branch)

        # Baseline (ADR 0006): el último escaneo COMPLETED de la rama por defecto. Mismo
        # orden que `baseline_fingerprints`: finished_at y, a igualdad, id.
        has_newer_baseline = exists().where(
            newer.c.repository_id == s.c.repository_id,
            newer.c.status == ScanStatus.COMPLETED.value,
            newer.c.ref.in_(default_refs(newer)),
            tuple_(newer.c.finished_at, newer.c.id) > tuple_(s.c.finished_at, s.c.id),
        )
        is_baseline = and_(
            s.c.status == ScanStatus.COMPLETED.value,
            s.c.ref.in_(default_refs(s)),
            ~has_newer_baseline,
        )
        referenced_by_finding = exists().where(
            or_(f.c.first_seen_scan_id == s.c.id, f.c.last_seen_scan_id == s.c.id)
        )
        supersedes_another = exists().where(superseding.c.superseded_by == s.c.id)
        return (
            select(s.c.id)
            .where(
                s.c.status.in_(_TERMINAL),
                s.c.finished_at < before,
                ~is_baseline,
                ~referenced_by_finding,
                ~supersedes_another,
            )
            .order_by(s.c.finished_at, s.c.id)
        )

    def _delete(self, statement: object) -> int:
        with translated_errors(), self._engine.begin() as conn:
            return conn.execute(statement).rowcount  # type: ignore[call-overload,no-any-return]


# Código SQLSTATE que usa `nave_purge_organization` cuando la organización no es purgable.
_NOT_PURGEABLE = "55000"


class SqlOrganizationPurgeStore:
    """Motor de `nave_maintenance`: puede leer `organizations` y ejecutar la función de
    purga, nada más. No tiene `DELETE` sobre ninguna tabla de negocio."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def candidates(self, uninstalled_before: datetime, limit: int) -> Sequence[PurgeCandidate]:
        o = t.organizations
        with translated_errors(), self._engine.connect() as conn:
            rows = conn.execute(
                select(o.c.id, o.c.uninstalled_at)
                .where(o.c.status == "UNINSTALLED", o.c.uninstalled_at < uninstalled_before)
                .order_by(o.c.uninstalled_at, o.c.id)
                .limit(limit)
            ).all()
        return [PurgeCandidate(OrganizationId(r.id), r.uninstalled_at) for r in rows]

    def purge_batch(self, organization_id: OrganizationId) -> PurgeProgress:
        with translated_errors(), self._engine.begin() as conn:
            try:
                result: Any = conn.execute(
                    text("SELECT public.nave_purge_organization(:org)"),
                    {"org": organization_id.value},
                ).scalar_one()
            except DBAPIError as error:
                # Se decide aquí, antes de que `translated_errors` lo trate como fallo
                # operativo: un rechazo de la función no es una caída de la base de datos.
                if getattr(error.orig, "sqlstate", None) == _NOT_PURGEABLE:
                    raise PurgeRefusedError("organization is not purgeable") from error
                raise
        return PurgeProgress(
            done=bool(result["done"]), deleted=int(result["deleted"]), step=str(result["step"])
        )


class SqlOrphanedUserStore:
    """Motor de `nave_maintenance`: solo ejecuta la función; no puede actualizar `users`."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def scrub_batch(self, last_login_before: datetime, limit: int) -> int:
        with translated_errors(), self._engine.begin() as conn:
            scrubbed: Any = conn.execute(
                text("SELECT public.nave_scrub_orphan_users(:before, :batch)"),
                {"before": last_login_before, "batch": limit},
            ).scalar_one()
        return int(scrubbed)
