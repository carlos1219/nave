"""DATOS DE DEMOSTRACIÓN: entradas de auditoría coherentes con demo_more.sql (solo desarrollo).

Se escriben con el propio `SqlAuditLog` para que la cadena de hashes siga siendo íntegra.
Se ejecuta dentro del contenedor de la API, que ya tiene la credencial de la base de datos:

    docker exec -i nave-api-1 python - < deploy/dev/demo_audit.py

Idempotente: si ya hay entradas de demostración (detalle `demo=2`), no hace nada. El audit
log es append-only: estas entradas no se pueden retirar después (ni siquiera con
demo_remove.sql), igual que cualquier otra.
"""

import os
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from nave.application.dto.audit import AuditEntry
from nave.bootstrap.config import EnvReader, read_secret_file
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.infrastructure.persistence.engine import create_database_engine
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory

DEMO_INSTALLATIONS = (999000001, 999000002, 999000003, 999000004)


def main() -> int:
    reader = EnvReader(os.environ, read_secret_file)
    engine = create_database_engine(
        reader.database_url("NAVE_DATABASE_URL", "NAVE_DATABASE_PASSWORD_FILE")
    )
    uow = SqlUnitOfWorkFactory(engine)
    with engine.connect() as conn:
        if conn.execute(text("SELECT 1 FROM audit_log WHERE details->>'demo' = '2' LIMIT 1")).first():
            sys.stdout.write("ya cargado\n")
            return 0
        orgs = conn.execute(
            text("SELECT id FROM organizations WHERE github_installation_id = ANY(:ids)"),
            {"ids": list(DEMO_INSTALLATIONS)},
        ).scalars().all()
        memberships = conn.execute(
            text(
                "SELECT user_id, organization_id, role, granted_by, granted_at FROM memberships "
                "WHERE organization_id = ANY(:orgs) ORDER BY granted_at"
            ),
            {"orgs": orgs},
        ).all()
        decisions = conn.execute(
            text(
                "SELECT d.finding_id, d.state, d.decided_at, d.decided_by, r.organization_id "
                "FROM triage_decisions d JOIN findings f ON f.id = d.finding_id "
                "JOIN repositories r ON r.id = f.repository_id "
                "WHERE r.organization_id = ANY(:orgs) AND d.decided_by IS NOT NULL"
            ),
            {"orgs": orgs},
        ).all()
        policies = conn.execute(
            text(
                "SELECT organization_id, version, created_at FROM quality_gate_policies "
                "WHERE organization_id = ANY(:orgs)"
            ),
            {"orgs": orgs},
        ).all()

    now = datetime.now(UTC)
    entries: list[AuditEntry] = []
    for m in memberships:
        entries.append(
            AuditEntry(
                occurred_at=m.granted_at,
                action="membership.changed",
                actor=UserId(m.granted_by) if m.granted_by else None,
                organization_id=OrganizationId(m.organization_id),
                target_type="user",
                target_id=str(m.user_id),
                details={"previous": "NONE", "current": m.role, "demo": "2"},
            )
        )
    for d in decisions:
        entries.append(
            AuditEntry(
                occurred_at=d.decided_at,
                action="finding.triaged",
                actor=UserId(d.decided_by),
                organization_id=OrganizationId(d.organization_id),
                target_type="finding",
                target_id=str(d.finding_id),
                details={"state": d.state, "demo": "2"},
            )
        )
    for p in policies:
        entries.append(
            AuditEntry(
                occurred_at=p.created_at,
                action="policy.changed",
                actor=UserId(memberships[0].granted_by) if memberships and memberships[0].granted_by else None,
                organization_id=OrganizationId(p.organization_id),
                target_type="policy",
                target_id=str(p.organization_id),
                details={"version": str(p.version), "demo": "2"},
            )
        )
    # Algún intento con rol insuficiente, como los que registra el autorizador.
    viewers = [m for m in memberships if m.role == "VIEWER"]
    for index, m in enumerate(viewers[:3]):
        entries.append(
            AuditEntry(
                occurred_at=now - timedelta(hours=6 * (index + 1)),
                action="access.denied",
                actor=UserId(m.user_id),
                organization_id=OrganizationId(m.organization_id),
                target_type="finding",
                target_id="triage",
                details={"required_role": "TRIAGER", "member": "true", "demo": "2"},
            )
        )
    entries.sort(key=lambda e: e.occurred_at)
    with uow() as tx:
        for entry in entries:
            tx.audit.append(entry)
        tx.commit()
    sys.stdout.write(f"{len(entries)} entradas\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
