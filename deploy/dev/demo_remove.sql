-- Retira los datos de demostración de demo_data.sql y demo_more.sql (organizaciones con
-- installation_id 999000001-999000004, usuarios 'demo-user-%' y entregas de webhooks de demo).
-- Las entradas del audit log no se pueden borrar (append-only, ADR 0030): se quedan.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE demo_org_ids ON COMMIT DROP AS
  SELECT id FROM organizations WHERE github_installation_id BETWEEN 999000001 AND 999000004;
DELETE FROM triage_decisions WHERE finding_id IN (SELECT f.id FROM findings f JOIN repositories r ON r.id = f.repository_id WHERE r.organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM finding_occurrences WHERE finding_id IN (SELECT f.id FROM findings f JOIN repositories r ON r.id = f.repository_id WHERE r.organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM finding_occurrences WHERE scan_id IN (SELECT s.id FROM scans s JOIN repositories r ON r.id = s.repository_id WHERE r.organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM findings WHERE repository_id IN (SELECT id FROM repositories WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM scan_logs WHERE scan_id IN (SELECT s.id FROM scans s JOIN repositories r ON r.id = s.repository_id WHERE r.organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM policy_evaluations WHERE scan_id IN (SELECT s.id FROM scans s JOIN repositories r ON r.id = s.repository_id WHERE r.organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM scans WHERE repository_id IN (SELECT id FROM repositories WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM quality_gate_policies WHERE organization_id IN (SELECT id FROM demo_org_ids);
DELETE FROM repositories WHERE organization_id IN (SELECT id FROM demo_org_ids);
DELETE FROM memberships WHERE organization_id IN (SELECT id FROM demo_org_ids);
DELETE FROM organizations WHERE id IN (SELECT id FROM demo_org_ids);
DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE oidc_subject LIKE 'demo-user-%');
DELETE FROM memberships WHERE user_id IN (SELECT id FROM users WHERE oidc_subject LIKE 'demo-user-%');
DELETE FROM platform_admins WHERE user_id IN (SELECT id FROM users WHERE oidc_subject LIKE 'demo-user-%');
DELETE FROM users WHERE oidc_subject LIKE 'demo-user-%';
DELETE FROM webhook_deliveries WHERE delivery_id IN (SELECT md5('demo2-delivery-' || i)::uuid FROM generate_series(1, 150) AS i);
-- Los contadores no llevan marca: en desarrollo se vacían enteros.
DELETE FROM webhook_counters;
COMMIT;
