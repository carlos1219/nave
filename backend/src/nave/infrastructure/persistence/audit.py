"""Audit log append-only con encadenado de hashes (CLAUDE.md §12, modelo de datos).

`hash = sha256(prev_hash ‖ fila canónica)`. Borrar, reordenar o modificar una entrada
rompe la cadena a partir de ese punto, y `verify_audit_chain` lo detecta. La base de
datos impide además UPDATE y DELETE con un trigger (ver migración 0001).
"""

import hashlib
import json
from typing import Any, Final

from sqlalchemy import Connection, Row, insert, select, text

from nave.application.dto.audit import AuditEntry
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.infrastructure.persistence import schema as t

GENESIS_HASH: Final = "0" * 64
AUDIT_LOCK: Final = (2, 0)


def canonical_entry(entry: AuditEntry) -> str:
    return json.dumps(
        {
            "occurred_at": entry.occurred_at.isoformat(),
            "action": entry.action,
            "actor": str(entry.actor.value) if entry.actor else None,
            "organization_id": str(entry.organization_id.value) if entry.organization_id else None,
            "target_type": entry.target_type,
            "target_id": entry.target_id,
            "details": dict(sorted(entry.details.items())),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def chain_hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


class SqlAuditLog:
    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def append(self, entry: AuditEntry) -> None:
        # Un único escritor a la vez: con READ COMMITTED, dos inserciones concurrentes
        # leerían el mismo último hash y bifurcarían la cadena. El lock consultivo es de
        # transacción: se libera en el commit, cuando la nueva fila ya es visible.
        self._conn.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": AUDIT_LOCK[0], "key": AUDIT_LOCK[1]},
        )
        prev: str | None = self._conn.execute(
            select(t.audit_log.c.hash).order_by(t.audit_log.c.id.desc()).limit(1)
        ).scalar_one_or_none()
        prev_hash = prev if prev is not None else GENESIS_HASH
        canonical = canonical_entry(entry)
        self._conn.execute(
            insert(t.audit_log).values(
                occurred_at=entry.occurred_at,
                action=entry.action,
                actor=entry.actor.value if entry.actor else None,
                organization_id=entry.organization_id.value if entry.organization_id else None,
                target_type=entry.target_type,
                target_id=entry.target_id,
                details=dict(entry.details),
                prev_hash=prev_hash,
                hash=chain_hash(prev_hash, canonical),
            )
        )


def verify_audit_chain(connection: Connection) -> int | None:
    """Recorre la cadena. Devuelve `None` si es íntegra o el `id` de la primera fila que
    no cuadra. Recalcula desde los datos, no se fía de `prev_hash` almacenado."""
    expected_prev = GENESIS_HASH
    rows = connection.execute(select(t.audit_log).order_by(t.audit_log.c.id))
    for row in rows:
        entry = _entry_from_row(row)
        if row.prev_hash != expected_prev or row.hash != chain_hash(
            expected_prev, canonical_entry(entry)
        ):
            return int(row.id)
        expected_prev = row.hash
    return None


def _entry_from_row(row: Row[Any]) -> AuditEntry:
    data = row._mapping
    return AuditEntry(
        occurred_at=data["occurred_at"],
        action=data["action"],
        actor=UserId(data["actor"]) if data["actor"] else None,
        organization_id=OrganizationId(data["organization_id"])
        if data["organization_id"]
        else None,
        target_type=data["target_type"],
        target_id=data["target_id"],
        details=data["details"],
    )
