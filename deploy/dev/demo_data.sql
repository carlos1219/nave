-- DATOS DE DEMOSTRACIÓN (solo desarrollo local; nunca en producción).
-- Crea la organización ficticia `demo-corp` con repositorios, escaneos y hallazgos de ejemplo
-- y da rol ADMIN al último usuario que inició sesión. Es idempotente. Retirar: demo_remove.sql.
-- Marca de demo: github_installation_id = 999000001.
\set ON_ERROR_STOP on
BEGIN;

DO $demo$
DECLARE
  v_user uuid;
  v_org  constant uuid := '0d3e0000-0000-4000-8000-000000000001';
  v_repo constant uuid := '0d3e0000-0000-4000-8000-0000000000a1';
  v_repo2 constant uuid := '0d3e0000-0000-4000-8000-0000000000a2';
  v_repo3 constant uuid := '0d3e0000-0000-4000-8000-0000000000a3';
  v_s1 constant uuid := '0d3e0000-0000-4000-8000-0000000000b1';
  v_s2 constant uuid := '0d3e0000-0000-4000-8000-0000000000b2';
  v_s3 constant uuid := '0d3e0000-0000-4000-8000-0000000000b3';
  v_s4 constant uuid := '0d3e0000-0000-4000-8000-0000000000b4';
  v_s5 constant uuid := '0d3e0000-0000-4000-8000-0000000000b5';
  v_s6 constant uuid := '0d3e0000-0000-4000-8000-0000000000b6';
  v_now timestamptz := now();
BEGIN
  SELECT id INTO v_user FROM users ORDER BY last_login_at DESC NULLS LAST LIMIT 1;
  IF v_user IS NULL THEN
    RAISE EXCEPTION 'No hay ningún usuario: inicia sesión una vez en https://localhost y repite.';
  END IF;

  INSERT INTO organizations (id, github_installation_id, account_login, status)
  VALUES (v_org, 999000001, 'demo-corp', 'ACTIVE') ON CONFLICT DO NOTHING;

  INSERT INTO memberships (user_id, organization_id, role, granted_by, granted_at)
  VALUES (v_user, v_org, 'ADMIN', NULL, v_now) ON CONFLICT DO NOTHING;

  INSERT INTO repositories (id, organization_id, github_repo_id, owner, name, default_branch, enabled) VALUES
    (v_repo,  v_org, 990000001, 'demo-corp', 'web-api',    'main', true),
    (v_repo2, v_org, 990000002, 'demo-corp', 'billing',    'main', true),
    (v_repo3, v_org, 990000003, 'demo-corp', 'mobile-app', 'main', false)
  ON CONFLICT DO NOTHING;

  -- Escaneos de web-api: uno anterior completado, uno reciente completado y uno fallido.
  INSERT INTO scans (id, repository_id, commit_sha, ref, trigger, trigger_key, status, queued_at,
                     requested_by, started_at, finished_at, status_reason_code, status_reason_detail,
                     summary, version) VALUES
    (v_s1, v_repo, '9f2c1a7e4b8d03a65c1e7f9a2b4d6e8f0a1c3e5b', 'main', 'MANUAL', 'demo-1', 'COMPLETED',
     v_now - interval '3 days', v_user, v_now - interval '3 days' + interval '5 seconds',
     v_now - interval '3 days' + interval '3 minutes', NULL, NULL,
     '{"new":{"critical":1,"high":2,"medium":3,"low":1,"info":0},"open":{"critical":1,"high":2,"medium":3,"low":1,"info":0},"verdict":"FAIL","truncated":false}', 1),
    (v_s2, v_repo, 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678', 'main', 'MANUAL', 'demo-2', 'COMPLETED',
     v_now - interval '2 hours', v_user, v_now - interval '2 hours' + interval '4 seconds',
     v_now - interval '2 hours' + interval '2 minutes', NULL, NULL,
     '{"new":{"critical":0,"high":1,"medium":0,"low":0,"info":1},"open":{"critical":1,"high":3,"medium":3,"low":1,"info":1},"verdict":"FAIL","truncated":false}', 1),
    (v_s3, v_repo, '0123456789abcdef0123456789abcdef01234567', 'feature/login', 'MANUAL', 'demo-3', 'FAILED',
     v_now - interval '1 day', v_user, v_now - interval '1 day' + interval '3 seconds',
     v_now - interval '1 day' + interval '40 seconds', 'FETCH_FAILED', 'No se pudo clonar el repositorio (demo)', NULL, 1),
    (v_s4, v_repo2, 'fedcba9876543210fedcba9876543210fedcba98', 'main', 'MANUAL', 'demo-4', 'COMPLETED',
     v_now - interval '5 hours', v_user, v_now - interval '5 hours' + interval '2 seconds',
     v_now - interval '5 hours' + interval '90 seconds', NULL, NULL,
     '{"new":{"critical":0,"high":0,"medium":1,"low":0,"info":0},"open":{"critical":0,"high":0,"medium":1,"low":0,"info":0},"verdict":"PASS","truncated":false}', 1)
  ON CONFLICT DO NOTHING;

  -- Política de quality gates: la versión 1 quedó sustituida por la 2, que añade plazos.
  INSERT INTO quality_gate_policies (id, organization_id, version, document, active, created_at) VALUES
    ('0d3e0000-0000-4000-8000-0000000000c1', v_org, 1,
     '{"version":1,"gates":[{"id":"max-critical","scope":"all_open","max":{"CRITICAL":0}}],"sla_days":{},"fail_on_sla_breach":false}',
     false, v_now - interval '30 days'),
    ('0d3e0000-0000-4000-8000-0000000000c2', v_org, 2,
     '{"version":1,"gates":[{"id":"max-critical","scope":"all_open","max":{"CRITICAL":0}},{"id":"no-new-high","scope":"new_findings","max":{"HIGH":0}}],"sla_days":{"CRITICAL":7,"HIGH":14,"MEDIUM":60},"fail_on_sla_breach":false}',
     true, v_now - interval '10 days')
  ON CONFLICT DO NOTHING;

  INSERT INTO policy_evaluations (scan_id, policy_id, verdict, reasons) VALUES
    (v_s1, NULL, 'FAIL', '[{"kind":"GATE","rule":"max-critical","blocking":true,"severity":"CRITICAL","observed":1,"limit":0}]'),
    (v_s2, NULL, 'FAIL', '[{"kind":"GATE","rule":"max-critical","blocking":true,"severity":"CRITICAL","observed":1,"limit":0},{"kind":"SLA","rule":"high-older-than-14d","blocking":false,"severity":"HIGH","observed":3,"limit":0}]'),
    (v_s4, NULL, 'PASS', '[]')
  ON CONFLICT DO NOTHING;

  INSERT INTO scan_logs (scan_id, seq, at, level, message) VALUES
    (v_s2, 1, v_now - interval '2 hours' + interval '4 seconds',  'INFO',    'fetch: clonando repositorio'),
    (v_s2, 2, v_now - interval '2 hours' + interval '20 seconds', 'INFO',    'semgrep 1.180.0: 214 reglas cargadas'),
    (v_s2, 3, v_now - interval '2 hours' + interval '70 seconds', 'WARNING', 'trivy: la base de datos de vulnerabilidades tiene 3 días'),
    (v_s2, 4, v_now - interval '2 hours' + interval '118 seconds','INFO',    'procesados 9 hallazgos; veredicto FAIL')
  ON CONFLICT DO NOTHING;

  -- Hallazgos de web-api (fingerprint de demostración: md5 duplicado).
  INSERT INTO findings (id, repository_id, fingerprint, scanner_kind, category, rule_id, severity, title,
                        first_seen_scan_id, last_seen_scan_id, first_seen_at, last_seen_at, cwe, cve,
                        triage_state, triage_expires_at, version)
  SELECT ('0d3e0000-0000-4000-8000-0000000001' || lpad(n::text, 2, '0'))::uuid, v_repo,
         md5('demo' || n) || md5('fp' || n), scanner, cat, rule, sev, title,
         v_s1, v_s2, v_now - interval '3 days', v_now - interval '2 hours', cwe, cve, st,
         CASE WHEN st = 'ACCEPTED_RISK' THEN v_now + interval '60 days' END, 1
  FROM (VALUES
    (1, 'semgrep', 'SAST', 'python.lang.security.audit.dangerous-subprocess-use', 'CRITICAL', 'Inyección de comandos en subprocess con shell=True', ARRAY['CWE-78'], ARRAY[]::varchar[], 'OPEN'),
    (2, 'semgrep', 'SAST', 'python.flask.security.injection.sql-injection', 'HIGH', 'Consulta SQL construida con concatenación de cadenas', ARRAY['CWE-89'], ARRAY[]::varchar[], 'OPEN'),
    (3, 'semgrep', 'SAST', 'python.jwt.security.unverified-jwt-decode', 'HIGH', 'JWT decodificado sin verificar la firma', ARRAY['CWE-347'], ARRAY[]::varchar[], 'OPEN'),
    (4, 'trivy',   'DEPENDENCY',  'CVE-2025-41234', 'HIGH', 'requests 2.19.0: divulgación de credenciales en redirecciones', ARRAY['CWE-200'], ARRAY['CVE-2025-41234'], 'OPEN'),
    (5, 'trivy',   'DEPENDENCY',  'CVE-2025-30871', 'MEDIUM', 'urllib3 1.24: desbordamiento en el análisis de cabeceras', ARRAY['CWE-120'], ARRAY['CVE-2025-30871'], 'ACCEPTED_RISK'),
    (6, 'semgrep', 'SAST', 'python.cryptography.security.insecure-hash-md5', 'MEDIUM', 'Uso de MD5 para derivar identificadores', ARRAY['CWE-328'], ARRAY[]::varchar[], 'OPEN'),
    (7, 'semgrep', 'SAST', 'python.flask.security.audit.debug-enabled', 'MEDIUM', 'Flask con debug=True en el punto de entrada', ARRAY['CWE-489'], ARRAY[]::varchar[], 'FALSE_POSITIVE'),
    (8, 'semgrep', 'SAST', 'generic.secrets.security.detected-generic-secret', 'LOW', 'Posible secreto en un fichero de ejemplo', ARRAY['CWE-798'], ARRAY[]::varchar[], 'OPEN'),
    (9, 'trivy',   'DEPENDENCY',  'CVE-2024-22190', 'INFO', 'GitPython: fallo menor de validación (sin explotación conocida)', ARRAY[]::varchar[], ARRAY['CVE-2024-22190'], 'OPEN')
  ) AS t(n, scanner, cat, rule, sev, title, cwe, cve, st)
  ON CONFLICT DO NOTHING;

  INSERT INTO finding_occurrences (scan_id, finding_id, path, start_line, end_line, message, snippet, is_new)
  SELECT v_s2, f.id, p.path, p.line, p.line,
         'Detectado por la regla ' || f.rule_id || ' (dato de demostración).',
         p.snippet, f.first_seen_scan_id = v_s2
  FROM findings f
  JOIN (VALUES
    (1, 'src/app/jobs/export.py', 42, 'subprocess.run(cmd, shell=True)'),
    (2, 'src/app/db/queries.py', 118, 'cur.execute("SELECT * FROM users WHERE id = " + user_id)'),
    (3, 'src/app/auth/tokens.py', 27, 'jwt.decode(token, options={"verify_signature": False})'),
    (4, 'requirements.txt', 7, 'requests==2.19.0'),
    (5, 'requirements.txt', 9, 'urllib3==1.24'),
    (6, 'src/app/util/ids.py', 10, 'hashlib.md5(value.encode()).hexdigest()'),
    (7, 'src/app/main.py', 5, 'app.run(debug=True)'),
    (8, 'docs/examples/.env.example', 3, 'API_KEY=changeme-example'),
    (9, 'requirements.txt', 14, 'GitPython==3.1.30')
  ) AS p(n, path, line, snippet)
    ON f.id = ('0d3e0000-0000-4000-8000-0000000001' || lpad(p.n::text, 2, '0'))::uuid
  ON CONFLICT DO NOTHING;

  INSERT INTO triage_decisions (id, finding_id, state, decided_at, decided_by, justification, expires_at) VALUES
    ('0d3e0000-0000-4000-8000-0000000002a1', '0d3e0000-0000-4000-8000-000000000105', 'ACCEPTED_RISK',
     v_now - interval '1 day', v_user, 'Solo accesible desde la red interna (demo).', v_now + interval '60 days'),
    ('0d3e0000-0000-4000-8000-0000000002a2', '0d3e0000-0000-4000-8000-000000000107', 'FALSE_POSITIVE',
     v_now - interval '1 day', v_user, 'Solo se usa en el entorno local de desarrollo (demo).', NULL)
  ON CONFLICT DO NOTHING;

  -- ADR 0029: una PR (#42) con un hallazgo nuevo y un escaneo de billing agotado que se
  -- resolvió con el siguiente. El fallo de feature/login sigue sin resolver.
  INSERT INTO scans (id, repository_id, commit_sha, ref, trigger, trigger_key, status, queued_at,
                     pull_request_number, requested_by, started_at, finished_at, status_reason_code,
                     status_reason_detail, summary, version) VALUES
    (v_s5, v_repo, 'c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00', 'feature/payments', 'WEBHOOK', 'demo-5', 'COMPLETED',
     v_now - interval '3 hours', 42, NULL, v_now - interval '3 hours' + interval '3 seconds',
     v_now - interval '3 hours' + interval '100 seconds', NULL, NULL,
     '{"new":{"critical":0,"high":1,"medium":0,"low":0,"info":0},"open":{"critical":1,"high":3,"medium":2,"low":1,"info":1},"verdict":"FAIL","truncated":false}', 1),
    (v_s6, v_repo2, 'beefbeefbeefbeefbeefbeefbeefbeefbeefbeef', 'main', 'MANUAL', 'demo-6', 'TIMED_OUT',
     v_now - interval '8 hours', NULL, v_user, v_now - interval '8 hours' + interval '2 seconds',
     v_now - interval '8 hours' + interval '15 minutes', 'TIMEOUT', 'Superó 900 s (demo)', NULL, 1)
  ON CONFLICT DO NOTHING;

  INSERT INTO policy_evaluations (scan_id, policy_id, verdict, reasons) VALUES
    (v_s5, NULL, 'FAIL', '[{"kind":"GATE","rule":"no-new-high","blocking":true,"severity":"HIGH","observed":1,"limit":0}]')
  ON CONFLICT DO NOTHING;

  INSERT INTO finding_occurrences (scan_id, finding_id, path, start_line, end_line, message, snippet, is_new) VALUES
    (v_s5, '0d3e0000-0000-4000-8000-000000000102', 'src/app/payments/refunds.py', 64, 64,
     'Consulta SQL construida con concatenación en el nuevo endpoint de reembolsos (demo).',
     'cur.execute("DELETE FROM refunds WHERE id = " + refund_id)', true),
    (v_s5, '0d3e0000-0000-4000-8000-000000000101', 'src/app/jobs/export.py', 42, 42,
     'Detectado por la regla python.lang.security.audit.dangerous-subprocess-use (dato de demostración).',
     'subprocess.run(cmd, shell=True)', false)
  ON CONFLICT DO NOTHING;

  -- Versiones de escáner de cada escaneo completado (el más antiguo, con un Semgrep anterior).
  UPDATE scans SET summary = summary || jsonb_build_object('tools', jsonb_build_array(
      jsonb_build_object('scanner', 'semgrep', 'version', CASE WHEN id = v_s1 THEN '1.179.0' ELSE '1.180.0' END,
                         'image_digest', 'sha256:' || repeat(CASE WHEN id = v_s1 THEN '1' ELSE '5' END, 64),
                         'findings', CASE WHEN id = v_s4 THEN 1 ELSE 6 END),
      jsonb_build_object('scanner', 'trivy', 'version', '0.58.0', 'image_digest', 'sha256:' || repeat('7', 64),
                         'findings', CASE WHEN id = v_s4 THEN 0 ELSE 3 END)))
  WHERE id IN (v_s1, v_s2, v_s4, v_s5) AND summary IS NOT NULL AND NOT summary ? 'tools';
END
$demo$;

COMMIT;
