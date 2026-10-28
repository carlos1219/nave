"""scrub personal data of users without any membership (data minimisation)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12 20:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tras purgar una organización (RR-11) o quitar a una persona de todas, su nombre y correo
# dejan de tener finalidad. La fila se conserva (`audit_log` y `triage_decisions` la
# referencian y no se reescriben); solo se vacían los datos personales, que el siguiente
# login vuelve a rellenar. `nave_maintenance` no puede actualizar `users`: solo ejecutar esta
# función, que además impone el plazo mínimo de 30 días sin login, no toca usuarios con
# sesión viva o con alguna pertenencia y trabaja por lotes acotados.
_FUNCTION = """
CREATE FUNCTION nave_scrub_orphan_users(p_before timestamptz, p_batch integer DEFAULT 1000)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_batch integer := least(greatest(coalesce(p_batch, 1000), 1), 10000);
    v_floor timestamptz := pg_catalog.now() - interval '30 days';
    v_before timestamptz := least(coalesce(p_before, v_floor), v_floor);
    v_count integer;
BEGIN
    WITH pick AS (
        SELECT u.id FROM public.users u
         WHERE (u.display_name IS NOT NULL OR u.email IS NOT NULL)
           AND (u.last_login_at IS NULL OR u.last_login_at < v_before)
           AND NOT EXISTS (SELECT 1 FROM public.memberships m WHERE m.user_id = u.id)
           AND NOT EXISTS (
               SELECT 1 FROM public.sessions s
                WHERE s.user_id = u.id AND s.expires_at > pg_catalog.now())
         ORDER BY u.id
         LIMIT v_batch
         FOR UPDATE OF u SKIP LOCKED
    )
    UPDATE public.users u
       SET display_name = NULL, email = NULL
      FROM pick WHERE u.id = pick.id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$$
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION nave_scrub_orphan_users(timestamptz, integer) FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_maintenance') THEN
                GRANT EXECUTE ON FUNCTION nave_scrub_orphan_users(timestamptz, integer)
                    TO nave_maintenance;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS nave_scrub_orphan_users(timestamptz, integer)")
