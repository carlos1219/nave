"""purge of uninstalled organizations (RR-11, ADR 0014)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12 13:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `nave_maintenance` no tiene DELETE sobre findings, triage_decisions ni repositories (RR-10).
# Esta función, de la que solo recibe EXECUTE, es el único camino para borrar una
# organización, y la propia función exige que esté desinstalada hace más de 30 días: el
# plazo vive aquí, no en el llamante, de modo que un worker comprometido no puede borrar
# inquilinos activos. Cada llamada borra un lote acotado y devuelve si terminó; las
# llamadas son transacciones independientes, así que una organización enorme avanza
# aunque una ejecución se corte. `audit_log` no se toca (sin FK, append-only).
_FUNCTION = """
CREATE FUNCTION nave_purge_organization(p_org uuid, p_batch integer DEFAULT 5000)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_status text;
    v_since timestamptz;
    v_batch integer := least(greatest(coalesce(p_batch, 5000), 1), 50000);
    v_count bigint;
BEGIN
    SELECT o.status, o.uninstalled_at INTO v_status, v_since
      FROM public.organizations o WHERE o.id = p_org FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('done', true, 'step', 'absent', 'deleted', 0);
    END IF;
    IF v_status IS DISTINCT FROM 'UNINSTALLED'
       OR v_since IS NULL
       OR v_since > pg_catalog.now() - interval '30 days' THEN
        RAISE EXCEPTION 'organization is not purgeable' USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;

    WITH d AS (
        DELETE FROM public.triage_decisions WHERE id IN (
            SELECT td.id FROM public.triage_decisions td
              JOIN public.findings f ON f.id = td.finding_id
              JOIN public.repositories r ON r.id = f.repository_id
             WHERE r.organization_id = p_org LIMIT v_batch) RETURNING 1)
    SELECT count(*) INTO v_count FROM d;
    IF v_count > 0 THEN
        RETURN jsonb_build_object('done', false, 'step', 'triage_decisions', 'deleted', v_count);
    END IF;

    WITH d AS (
        DELETE FROM public.finding_occurrences WHERE ctid IN (
            SELECT fo.ctid FROM public.finding_occurrences fo
              JOIN public.scans s ON s.id = fo.scan_id
              JOIN public.repositories r ON r.id = s.repository_id
             WHERE r.organization_id = p_org LIMIT v_batch) RETURNING 1)
    SELECT count(*) INTO v_count FROM d;
    IF v_count > 0 THEN
        RETURN jsonb_build_object('done', false, 'step', 'finding_occurrences', 'deleted', v_count);
    END IF;

    WITH d AS (
        DELETE FROM public.findings WHERE id IN (
            SELECT f.id FROM public.findings f
              JOIN public.repositories r ON r.id = f.repository_id
             WHERE r.organization_id = p_org LIMIT v_batch) RETURNING 1)
    SELECT count(*) INTO v_count FROM d;
    IF v_count > 0 THEN
        RETURN jsonb_build_object('done', false, 'step', 'findings', 'deleted', v_count);
    END IF;

    WITH d AS (
        DELETE FROM public.policy_evaluations WHERE scan_id IN (
            SELECT s.id FROM public.scans s
              JOIN public.repositories r ON r.id = s.repository_id
             WHERE r.organization_id = p_org LIMIT v_batch) RETURNING 1)
    SELECT count(*) INTO v_count FROM d;
    IF v_count > 0 THEN
        RETURN jsonb_build_object('done', false, 'step', 'policy_evaluations', 'deleted', v_count);
    END IF;

    WITH d AS (
        DELETE FROM public.scan_logs WHERE ctid IN (
            SELECT sl.ctid FROM public.scan_logs sl
              JOIN public.scans s ON s.id = sl.scan_id
              JOIN public.repositories r ON r.id = s.repository_id
             WHERE r.organization_id = p_org LIMIT v_batch) RETURNING 1)
    SELECT count(*) INTO v_count FROM d;
    IF v_count > 0 THEN
        RETURN jsonb_build_object('done', false, 'step', 'scan_logs', 'deleted', v_count);
    END IF;

    -- scans.superseded_by apunta a otro escaneo de la misma organización: se desengancha
    -- antes de borrar para no depender del orden de los lotes.
    WITH d AS (
        UPDATE public.scans SET superseded_by = NULL WHERE id IN (
            SELECT s.id FROM public.scans s
              JOIN public.repositories r ON r.id = s.repository_id
             WHERE r.organization_id = p_org AND s.superseded_by IS NOT NULL
             LIMIT v_batch) RETURNING 1)
    SELECT count(*) INTO v_count FROM d;
    IF v_count > 0 THEN
        RETURN jsonb_build_object('done', false, 'step', 'scans_unlink', 'deleted', 0);
    END IF;

    WITH d AS (
        DELETE FROM public.scans WHERE id IN (
            SELECT s.id FROM public.scans s
              JOIN public.repositories r ON r.id = s.repository_id
             WHERE r.organization_id = p_org LIMIT v_batch) RETURNING 1)
    SELECT count(*) INTO v_count FROM d;
    IF v_count > 0 THEN
        RETURN jsonb_build_object('done', false, 'step', 'scans', 'deleted', v_count);
    END IF;

    DELETE FROM public.quality_gate_policies WHERE organization_id = p_org;
    DELETE FROM public.memberships WHERE organization_id = p_org;
    DELETE FROM public.repositories WHERE organization_id = p_org;
    DELETE FROM public.organizations WHERE id = p_org;
    RETURN jsonb_build_object('done', true, 'step', 'organization', 'deleted', 1);
END
$$
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION nave_purge_organization(uuid, integer) FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'nave_maintenance') THEN
                GRANT EXECUTE ON FUNCTION nave_purge_organization(uuid, integer)
                    TO nave_maintenance;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS nave_purge_organization(uuid, integer)")
