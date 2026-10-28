-- DATOS DE DEMOSTRACIÓN AMPLIADOS (solo desarrollo local; nunca en producción).
-- Requiere haber cargado antes demo_data.sql. Añade tres organizaciones (nimbus-labs,
-- orbital-pay y legacy-systems, esta suspendida), dos repositorios más en demo-corp, ocho
-- usuarios ficticios, 30 días de escaneos con su evolución, hallazgos de SAST, dependencias
-- e IaC con triage variado, pull requests, fallos, entregas de webhooks y contadores.
-- Idempotente: identificadores deterministas (md5) y ON CONFLICT DO NOTHING.
-- Marcas: github_installation_id 999000002-999000004, repositorios 990001001-990001011,
-- usuarios con oidc_subject 'demo-user-%'. Retirar: demo_remove.sql.
\set ON_ERROR_STOP on
BEGIN;

DO $demo$
DECLARE
  v_dev uuid;
  v_now timestamptz := date_trunc('minute', now());
  v_org1 constant uuid := '0d3e0000-0000-4000-8000-000000000001';
  v_issuer text;
BEGIN
  SELECT id, oidc_issuer INTO v_dev, v_issuer FROM users
   WHERE oidc_subject NOT LIKE 'demo-user-%' ORDER BY last_login_at DESC NULLS LAST LIMIT 1;
  IF v_dev IS NULL OR NOT EXISTS (SELECT 1 FROM organizations WHERE id = v_org1) THEN
    RAISE EXCEPTION 'Carga antes demo_data.sql (y entra una vez en https://localhost).';
  END IF;

  -- Organizaciones -------------------------------------------------------------------
  CREATE TEMP TABLE demo_orgs (k int PRIMARY KEY, id uuid, login text, inst bigint, status text) ON COMMIT DROP;
  INSERT INTO demo_orgs VALUES
    (1, v_org1, 'demo-corp', 999000001, 'ACTIVE'),
    (2, md5('demo2-org-2')::uuid, 'nimbus-labs', 999000002, 'ACTIVE'),
    (3, md5('demo2-org-3')::uuid, 'orbital-pay', 999000003, 'ACTIVE'),
    (4, md5('demo2-org-4')::uuid, 'legacy-systems', 999000004, 'SUSPENDED');
  INSERT INTO organizations (id, github_installation_id, account_login, status)
  SELECT id, inst, login, status FROM demo_orgs WHERE k > 1 ON CONFLICT DO NOTHING;

  -- Repositorios ---------------------------------------------------------------------
  CREATE TEMP TABLE demo_repos (r int PRIMARY KEY, org int, name text, id uuid, enabled bool) ON COMMIT DROP;
  INSERT INTO demo_repos (r, org, name, enabled) VALUES
    (1, 2, 'platform-api', true), (2, 2, 'web-portal', true), (3, 2, 'mobile-gateway', true),
    (4, 2, 'infra-terraform', true), (5, 3, 'payments-core', true), (6, 3, 'checkout-web', true),
    (7, 3, 'fraud-engine', true), (8, 4, 'erp-monolith', true), (9, 4, 'reports-batch', false),
    (10, 1, 'auth-service', true), (11, 1, 'data-pipeline', true);
  UPDATE demo_repos SET id = md5('demo2-repo-' || r)::uuid;
  INSERT INTO repositories (id, organization_id, github_repo_id, owner, name, default_branch, enabled)
  SELECT d.id, o.id, 990001000 + d.r, o.login, d.name, 'main', d.enabled
    FROM demo_repos d JOIN demo_orgs o ON o.k = d.org
  ON CONFLICT DO NOTHING;

  -- Usuarios ficticios (no pueden iniciar sesión: no existen en el proveedor OIDC) ----
  CREATE TEMP TABLE demo_users (u int PRIMARY KEY, id uuid, name text, email text, last_days int) ON COMMIT DROP;
  INSERT INTO demo_users (u, name, email, last_days) VALUES
    (1, 'Lucía Martín', 'lucia.martin@example.test', 0),
    (2, 'Javier Ortega', 'javier.ortega@example.test', 1),
    (3, 'Marta Gil', 'marta.gil@example.test', 0),
    (4, 'Andrés Prieto', 'andres.prieto@example.test', 3),
    (5, 'Sofía Navarro', 'sofia.navarro@example.test', 0),
    (6, 'Diego Ramos', 'diego.ramos@example.test', 9),
    (7, 'Elena Vidal', 'elena.vidal@example.test', 2),
    (8, 'Pablo Serrano', 'pablo.serrano@example.test', 41);
  UPDATE demo_users SET id = md5('demo2-user-' || u)::uuid;
  INSERT INTO users (id, oidc_issuer, oidc_subject, display_name, email, last_login_at)
  SELECT id, v_issuer, 'demo-user-' || u, name, email, v_now - make_interval(days => last_days, hours => u)
    FROM demo_users ON CONFLICT DO NOTHING;

  INSERT INTO memberships (user_id, organization_id, role, granted_by, granted_at)
  SELECT u.id, o.id, m.role, v_dev, v_now - make_interval(days => 60 - m.u * 3)
    FROM (VALUES
      (1, 1, 'TRIAGER'), (2, 1, 'VIEWER'), (3, 1, 'ADMIN'),
      (1, 2, 'ADMIN'), (4, 2, 'TRIAGER'), (5, 2, 'TRIAGER'), (6, 2, 'VIEWER'),
      (5, 3, 'ADMIN'), (7, 3, 'TRIAGER'), (2, 3, 'VIEWER'), (3, 3, 'VIEWER'),
      (8, 4, 'ADMIN'), (6, 4, 'VIEWER')
    ) AS m(u, org, role)
    JOIN demo_users u ON u.u = m.u JOIN demo_orgs o ON o.k = m.org
  ON CONFLICT DO NOTHING;
  -- El usuario de desarrollo entra en todas para poder verlas.
  INSERT INTO memberships (user_id, organization_id, role, granted_by, granted_at)
  SELECT v_dev, o.id, CASE o.k WHEN 2 THEN 'ADMIN' WHEN 3 THEN 'TRIAGER' ELSE 'VIEWER' END, NULL, v_now - interval '60 days'
    FROM demo_orgs o WHERE o.k > 1
  ON CONFLICT DO NOTHING;

  -- Sesiones vivas de tres usuarios (el gestor de usuarios las muestra y puede cerrarlas).
  INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, last_seen_at, expires_at)
  SELECT decode(md5('demo2-sess-' || s.n) || md5('demo2-sesx-' || s.n), 'hex'), u.id, 'demo-csrf',
         v_now - make_interval(hours => s.n), v_now - make_interval(mins => 5 * s.n), v_now + interval '10 hours'
    FROM (VALUES (1, 1), (2, 1), (3, 3), (4, 5)) AS s(n, u) JOIN demo_users u ON u.u = s.u
  ON CONFLICT DO NOTHING;

  -- Catálogo de hallazgos de ejemplo -------------------------------------------------
  CREATE TEMP TABLE demo_catalog (n int PRIMARY KEY, scanner text, cat text, rule text, sev text, title text,
                                  cwe varchar[], cve varchar[], path text, line int, snippet text) ON COMMIT DROP;
  INSERT INTO demo_catalog VALUES
    (1, 'semgrep', 'SAST', 'python.lang.security.audit.dangerous-subprocess-use', 'CRITICAL', 'Inyección de comandos en subprocess con shell=True', ARRAY['CWE-78'], ARRAY[]::varchar[], 'src/jobs/runner.py', 58, 'subprocess.run(f"tar -xf {name}", shell=True)'),
    (2, 'semgrep', 'SAST', 'python.flask.security.injection.sql-injection', 'HIGH', 'Consulta SQL construida con concatenación de cadenas', ARRAY['CWE-89'], ARRAY[]::varchar[], 'src/db/queries.py', 131, 'cur.execute("SELECT * FROM orders WHERE id = " + order_id)'),
    (3, 'semgrep', 'SAST', 'javascript.express.security.xss.direct-response-write', 'HIGH', 'Respuesta HTML con datos del usuario sin escapar', ARRAY['CWE-79'], ARRAY[]::varchar[], 'web/routes/profile.js', 42, 'res.send("<h1>" + req.query.name + "</h1>")'),
    (4, 'semgrep', 'SAST', 'java.lang.security.audit.xxe.documentbuilderfactory-disallow-doctype', 'HIGH', 'Parser XML sin protección frente a XXE', ARRAY['CWE-611'], ARRAY[]::varchar[], 'src/main/java/xml/Importer.java', 77, 'DocumentBuilderFactory.newInstance().newDocumentBuilder()'),
    (5, 'semgrep', 'SAST', 'go.lang.security.audit.crypto.math-random-used', 'MEDIUM', 'Generador aleatorio no criptográfico para tokens', ARRAY['CWE-338'], ARRAY[]::varchar[], 'internal/auth/token.go', 23, 'token := fmt.Sprint(rand.Int63())'),
    (6, 'semgrep', 'SAST', 'python.cryptography.security.insecure-hash-md5', 'MEDIUM', 'Uso de MD5 para firmar datos', ARRAY['CWE-328'], ARRAY[]::varchar[], 'src/util/sign.py', 12, 'hashlib.md5(payload).hexdigest()'),
    (7, 'semgrep', 'SAST', 'javascript.jsonwebtoken.security.jwt-none-alg', 'HIGH', 'JWT aceptado con el algoritmo none', ARRAY['CWE-347'], ARRAY[]::varchar[], 'api/auth/verify.ts', 19, 'jwt.verify(token, key, { algorithms: ["none", "HS256"] })'),
    (8, 'semgrep', 'SAST', 'python.requests.security.disabled-cert-validation', 'MEDIUM', 'Petición HTTPS con verify=False', ARRAY['CWE-295'], ARRAY[]::varchar[], 'src/clients/billing.py', 66, 'requests.post(url, json=body, verify=False)'),
    (9, 'semgrep', 'SECRET', 'generic.secrets.security.detected-aws-access-key', 'HIGH', 'Clave de acceso de AWS en el código', ARRAY['CWE-798'], ARRAY[]::varchar[], 'config/settings.py', 8, NULL),
    (10, 'trivy', 'DEPENDENCY', 'CVE-2024-24762', 'HIGH', 'fastapi 0.95.0: denegación de servicio en formularios multipart', ARRAY['CWE-400'], ARRAY['CVE-2024-24762'], 'requirements.txt', 4, 'fastapi==0.95.0'),
    (11, 'trivy', 'DEPENDENCY', 'CVE-2023-45857', 'MEDIUM', 'axios 0.27.2: filtración del token XSRF a terceros', ARRAY['CWE-352'], ARRAY['CVE-2023-45857'], 'package-lock.json', 812, '"axios": "0.27.2"'),
    (12, 'trivy', 'DEPENDENCY', 'CVE-2022-42889', 'CRITICAL', 'commons-text 1.9: ejecución remota de código (Text4Shell)', ARRAY['CWE-94'], ARRAY['CVE-2022-42889'], 'pom.xml', 88, '<version>1.9</version>'),
    (13, 'trivy', 'DEPENDENCY', 'CVE-2023-39325', 'HIGH', 'golang.org/x/net: agotamiento de recursos en HTTP/2', ARRAY['CWE-770'], ARRAY['CVE-2023-39325'], 'go.sum', 31, 'golang.org/x/net v0.15.0'),
    (14, 'trivy', 'DEPENDENCY', 'CVE-2024-3651', 'MEDIUM', 'idna 3.6: consumo excesivo de CPU con entradas manipuladas', ARRAY['CWE-400'], ARRAY['CVE-2024-3651'], 'requirements.txt', 11, 'idna==3.6'),
    (15, 'trivy', 'DEPENDENCY', 'CVE-2022-25883', 'LOW', 'semver 7.3.7: ReDoS al analizar rangos de versión', ARRAY['CWE-1333'], ARRAY['CVE-2022-25883'], 'package-lock.json', 1290, '"semver": "7.3.7"'),
    (16, 'trivy', 'IAC', 'AVD-DS-0002', 'MEDIUM', 'La imagen se ejecuta como root', ARRAY['CWE-250'], ARRAY[]::varchar[], 'Dockerfile', 1, 'FROM python:3.11'),
    (17, 'trivy', 'IAC', 'AVD-KSV-0017', 'HIGH', 'Contenedor privilegiado en el manifiesto de despliegue', ARRAY['CWE-250'], ARRAY[]::varchar[], 'k8s/deployment.yaml', 34, 'privileged: true'),
    (18, 'semgrep', 'SAST', 'python.flask.security.audit.debug-enabled', 'LOW', 'Flask con debug=True en el punto de entrada', ARRAY['CWE-489'], ARRAY[]::varchar[], 'src/app.py', 90, 'app.run(debug=True)'),
    (19, 'trivy', 'DEPENDENCY', 'CVE-2023-37920', 'INFO', 'certifi 2023.5.7: raíz de confianza retirada', ARRAY['CWE-345'], ARRAY['CVE-2023-37920'], 'requirements.txt', 2, 'certifi==2023.5.7'),
    (20, 'semgrep', 'SAST', 'typescript.react.security.audit.react-dangerouslysetinnerhtml', 'MEDIUM', 'HTML sin sanear en dangerouslySetInnerHTML', ARRAY['CWE-79'], ARRAY[]::varchar[], 'web/components/Comment.tsx', 27, '<div dangerouslySetInnerHTML={{ __html: body }} />');

  -- Escaneos de la rama por defecto: uno cada dos días durante 30 días ----------------
  CREATE TEMP TABLE demo_scans ON COMMIT DROP AS
  SELECT d.r, g.day, md5('demo2-scan-' || d.r || '-' || g.day)::uuid AS id,
         v_now - make_interval(days => g.day, mins => d.r * 37) - interval '3 hours' AS queued_at
    FROM demo_repos d CROSS JOIN generate_series(0, 28, 2) AS g(day)
   WHERE d.enabled;
  INSERT INTO scans (id, repository_id, commit_sha, ref, trigger, trigger_key, status, queued_at, requested_by,
                     started_at, finished_at, summary, version)
  SELECT s.id, d.id, md5('demo2-commit-' || s.r || '-' || s.day) || substr(md5('c' || s.r || s.day), 1, 8), 'main',
         CASE WHEN s.day % 6 = 0 THEN 'MANUAL' ELSE 'WEBHOOK' END, 'demo2-' || s.r || '-' || s.day, 'COMPLETED',
         s.queued_at, CASE WHEN s.day % 6 = 0 THEN v_dev END, s.queued_at + interval '4 seconds',
         s.queued_at + make_interval(secs => 95 + s.r * 7 + s.day),
         '{"new":{"critical":0,"high":0,"medium":0,"low":0,"info":0},"open":{"critical":0,"high":0,"medium":0,"low":0,"info":0},"verdict":"PASS","truncated":false}', 1
    FROM demo_scans s JOIN demo_repos d ON d.r = s.r
  ON CONFLICT DO NOTHING;

  -- Hallazgos ------------------------------------------------------------------------
  CREATE TEMP TABLE demo_findings ON COMMIT DROP AS
  SELECT d.r, c.n, d.id AS repo_id, md5('demo2-finding-' || d.r || '-' || c.n)::uuid AS id,
         c.scanner, c.cat, c.rule, c.sev, c.title, c.cwe, c.cve, c.path, c.line, c.snippet,
         CASE WHEN (c.n + d.r) % 11 = 0 THEN 0 ELSE 28 - 2 * ((c.n * 3 + d.r) % 14) END AS first_day,
         CASE (c.n * 7 + d.r) % 10 WHEN 6 THEN 'FIXED' WHEN 7 THEN 'FALSE_POSITIVE' WHEN 8 THEN 'ACCEPTED_RISK' ELSE 'OPEN' END AS state
    FROM demo_repos d CROSS JOIN demo_catalog c
   WHERE d.enabled AND (c.n + d.r) % 3 <> 0;
  -- Lo descubierto hoy no puede estar ya corregido.
  UPDATE demo_findings SET state = 'OPEN' WHERE first_day = 0 AND state = 'FIXED';
  ALTER TABLE demo_findings ADD COLUMN last_day int;
  UPDATE demo_findings SET last_day = CASE WHEN state = 'FIXED' THEN GREATEST(first_day - 6, 2) ELSE 0 END;
  UPDATE demo_findings SET last_day = first_day WHERE last_day > first_day;

  INSERT INTO findings (id, repository_id, fingerprint, scanner_kind, category, rule_id, severity, title,
                        first_seen_scan_id, last_seen_scan_id, first_seen_at, last_seen_at, cwe, cve,
                        triage_state, triage_expires_at, version)
  SELECT f.id, f.repo_id, md5('fp2-' || f.r || '-' || f.n) || md5('fq2-' || f.r || '-' || f.n), f.scanner, f.cat,
         f.rule, f.sev, f.title, fs.id, ls.id, fsc.finished_at, lsc.finished_at, f.cwe, f.cve, f.state,
         -- Aceptado al día siguiente de detectarse, por 10 a 79 días: unos caducados y otros no.
         CASE WHEN f.state = 'ACCEPTED_RISK'
              THEN fsc.finished_at + make_interval(days => 11 + (f.n * 13 + f.r * 7) % 70) END, 1
    FROM demo_findings f
    JOIN demo_scans fs ON fs.r = f.r AND fs.day = f.first_day
    JOIN demo_scans ls ON ls.r = f.r AND ls.day = f.last_day
    JOIN scans fsc ON fsc.id = fs.id
    JOIN scans lsc ON lsc.id = ls.id
  ON CONFLICT DO NOTHING;

  INSERT INTO finding_occurrences (scan_id, finding_id, path, start_line, end_line, message, snippet, is_new)
  SELECT s.id, f.id, f.path, f.line, f.line,
         'Detectado por ' || f.rule || ' (dato de demostración).', f.snippet, s.day = f.first_day
    FROM demo_findings f JOIN demo_scans s ON s.r = f.r AND s.day IN (f.first_day, f.last_day)
  ON CONFLICT DO NOTHING;

  -- Decisiones de triage: de personas (falsos positivos, riesgos) y del sistema (corregidos).
  INSERT INTO triage_decisions (id, finding_id, state, decided_at, decided_by, justification, expires_at)
  SELECT md5('demo2-dec-' || f.r || '-' || f.n)::uuid, f.id, f.state,
         -- Las personas deciden al día siguiente de la detección; el sistema marca FIXED en el
         -- primer escaneo que ya no lo ve (dos días después de la última detección).
         LEAST(CASE WHEN f.state = 'FIXED' THEN fi.last_seen_at + interval '2 days'
                    ELSE fi.first_seen_at + interval '1 day' END,
               v_now - interval '1 minute'),
         CASE WHEN f.state = 'FIXED' THEN NULL ELSE u.id END,
         CASE f.state
           WHEN 'FALSE_POSITIVE' THEN 'El valor nunca llega desde una entrada del usuario: lo fija la configuración (demo).'
           WHEN 'ACCEPTED_RISK' THEN 'Servicio solo accesible desde la red interna; corrección planificada para el próximo trimestre (demo).'
           ELSE 'Not observed in a later scan (demo).'
         END,
         fi.triage_expires_at
    FROM demo_findings f
    JOIN findings fi ON fi.id = f.id
    JOIN demo_repos d ON d.r = f.r
    LEFT JOIN demo_users u ON u.u = CASE d.org WHEN 1 THEN 1 WHEN 2 THEN 4 WHEN 3 THEN 7 ELSE 8 END
   WHERE f.state <> 'OPEN'
  ON CONFLICT DO NOTHING;
  -- Resumen de cada escaneo: abiertos y nuevos según los hallazgos que veía ese día.
  UPDATE scans sc SET summary = agg.summary FROM (
    SELECT s.id, jsonb_build_object(
      'new', jsonb_build_object(
        'critical', count(*) FILTER (WHERE f.first_day = s.day AND f.sev = 'CRITICAL'),
        'high', count(*) FILTER (WHERE f.first_day = s.day AND f.sev = 'HIGH'),
        'medium', count(*) FILTER (WHERE f.first_day = s.day AND f.sev = 'MEDIUM'),
        'low', count(*) FILTER (WHERE f.first_day = s.day AND f.sev = 'LOW'),
        'info', count(*) FILTER (WHERE f.first_day = s.day AND f.sev = 'INFO')),
      'open', jsonb_build_object(
        'critical', count(*) FILTER (WHERE f.sev = 'CRITICAL'),
        'high', count(*) FILTER (WHERE f.sev = 'HIGH'),
        'medium', count(*) FILTER (WHERE f.sev = 'MEDIUM'),
        'low', count(*) FILTER (WHERE f.sev = 'LOW'),
        'info', count(*) FILTER (WHERE f.sev = 'INFO')),
      'verdict', CASE WHEN count(*) FILTER (WHERE f.sev = 'CRITICAL') > 0 THEN 'FAIL' ELSE 'PASS' END,
      'truncated', false,
      'tools', jsonb_build_array(
        jsonb_build_object('scanner', 'semgrep', 'version', CASE WHEN s.day > 14 THEN '1.179.0' ELSE '1.180.0' END,
                           'image_digest', 'sha256:' || repeat(CASE WHEN s.day > 14 THEN '1' ELSE '5' END, 64),
                           'findings', count(*) FILTER (WHERE f.scanner = 'semgrep')),
        jsonb_build_object('scanner', 'trivy', 'version', '0.58.0', 'image_digest', 'sha256:' || repeat('7', 64),
                           'findings', count(*) FILTER (WHERE f.scanner = 'trivy')))) AS summary
      FROM demo_scans s
      LEFT JOIN demo_findings f ON f.r = s.r AND f.first_day >= s.day AND f.last_day <= s.day
                               AND f.state NOT IN ('FALSE_POSITIVE', 'ACCEPTED_RISK')
     GROUP BY s.id, s.day
  ) AS agg
   WHERE sc.id = agg.id;

  -- Pull requests: dos por repositorio, con algún hallazgo nuevo.
  CREATE TEMP TABLE demo_prs ON COMMIT DROP AS
  SELECT d.r, p.pr, 100 + d.r * 10 + p.pr AS number, md5('demo2-pr-' || d.r || '-' || p.pr)::uuid AS id,
         v_now - make_interval(hours => p.pr * 20 + d.r * 3) AS queued_at
    FROM demo_repos d CROSS JOIN (VALUES (1), (2)) AS p(pr) WHERE d.enabled;
  INSERT INTO scans (id, repository_id, commit_sha, ref, trigger, trigger_key, status, queued_at, pull_request_number,
                     started_at, finished_at, summary, version)
  SELECT p.id, d.id, md5('demo2-prc-' || p.r || '-' || p.pr) || substr(md5('p' || p.r || p.pr), 1, 8),
         'refs/pull/' || p.number || '/head', 'WEBHOOK', 'demo2-pr-' || p.r || '-' || p.pr, 'COMPLETED', p.queued_at,
         p.number, p.queued_at + interval '3 seconds', p.queued_at + make_interval(secs => 80 + p.r * 5),
         '{"new":{"critical":0,"high":0,"medium":0,"low":0,"info":0},"open":{"critical":0,"high":0,"medium":0,"low":0,"info":0},"verdict":"PASS","truncated":false}', 1
    FROM demo_prs p JOIN demo_repos d ON d.r = p.r
  ON CONFLICT DO NOTHING;
  INSERT INTO finding_occurrences (scan_id, finding_id, path, start_line, end_line, message, snippet, is_new)
  SELECT p.id, f.id, f.path, f.line + 3, f.line + 3,
         'Introducido en la PR #' || p.number || ' (dato de demostración).', f.snippet, (f.n + p.pr) % 4 = 0
    FROM demo_prs p JOIN demo_findings f ON f.r = p.r AND f.state = 'OPEN'
  ON CONFLICT DO NOTHING;
  UPDATE scans sc SET summary = agg.summary FROM (
    SELECT p.id, jsonb_build_object(
      'new', jsonb_build_object(
        'critical', count(*) FILTER (WHERE o.is_new AND f.sev = 'CRITICAL'),
        'high', count(*) FILTER (WHERE o.is_new AND f.sev = 'HIGH'),
        'medium', count(*) FILTER (WHERE o.is_new AND f.sev = 'MEDIUM'),
        'low', count(*) FILTER (WHERE o.is_new AND f.sev = 'LOW'),
        'info', count(*) FILTER (WHERE o.is_new AND f.sev = 'INFO')),
      'open', jsonb_build_object(
        'critical', count(*) FILTER (WHERE f.sev = 'CRITICAL'), 'high', count(*) FILTER (WHERE f.sev = 'HIGH'),
        'medium', count(*) FILTER (WHERE f.sev = 'MEDIUM'), 'low', count(*) FILTER (WHERE f.sev = 'LOW'),
        'info', count(*) FILTER (WHERE f.sev = 'INFO')),
      'verdict', CASE WHEN count(*) FILTER (WHERE o.is_new AND f.sev IN ('CRITICAL', 'HIGH')) > 0 THEN 'FAIL' ELSE 'PASS' END,
      'truncated', false,
      'tools', jsonb_build_array(
        jsonb_build_object('scanner', 'semgrep', 'version', '1.180.0', 'image_digest', 'sha256:' || repeat('5', 64),
                           'findings', count(*) FILTER (WHERE f.scanner = 'semgrep')),
        jsonb_build_object('scanner', 'trivy', 'version', '0.58.0', 'image_digest', 'sha256:' || repeat('7', 64),
                           'findings', count(*) FILTER (WHERE f.scanner = 'trivy')))) AS summary
      FROM demo_prs p
      JOIN finding_occurrences o ON o.scan_id = p.id
      JOIN demo_findings f ON f.id = o.finding_id
     GROUP BY p.id
  ) AS agg
   WHERE sc.id = agg.id;

  -- Fallos: unos resueltos por un escaneo posterior y otros no.
  INSERT INTO scans (id, repository_id, commit_sha, ref, trigger, trigger_key, status, queued_at, started_at,
                     finished_at, status_reason_code, status_reason_detail, version)
  SELECT md5('demo2-fail-' || d.r)::uuid, d.id, md5('demo2-failc-' || d.r) || substr(md5('f' || d.r), 1, 8),
         CASE WHEN d.r % 2 = 0 THEN 'main' ELSE 'release/2.' || d.r END, 'WEBHOOK', 'demo2-fail-' || d.r,
         CASE WHEN d.r % 3 = 0 THEN 'TIMED_OUT' ELSE 'FAILED' END,
         v_now - make_interval(days => d.r % 4 + 1, hours => d.r), v_now - make_interval(days => d.r % 4 + 1, hours => d.r) + interval '2 seconds',
         v_now - make_interval(days => d.r % 4 + 1, hours => d.r) + interval '15 minutes',
         CASE WHEN d.r % 3 = 0 THEN 'TIMEOUT' WHEN d.r % 3 = 1 THEN 'SCANNER_FAILED' ELSE 'FETCH_FAILED' END,
         CASE WHEN d.r % 3 = 0 THEN 'Superó 900 s de análisis (demo)'
              WHEN d.r % 3 = 1 THEN 'semgrep terminó con el código 2 (demo)'
              ELSE 'GitHub respondió 502 al clonar (demo)' END, 1
    FROM demo_repos d WHERE d.enabled AND d.r IN (1, 3, 5, 6, 10)
  ON CONFLICT DO NOTHING;

  -- Evaluaciones de los escaneos completados (quality gate).
  INSERT INTO policy_evaluations (scan_id, policy_id, verdict, reasons)
  SELECT sc.id, NULL, sc.summary ->> 'verdict',
         CASE WHEN sc.summary ->> 'verdict' = 'FAIL'
              THEN jsonb_build_array(jsonb_build_object('kind', 'GATE', 'rule', 'max-critical', 'blocking', true,
                     'severity', 'CRITICAL', 'observed', (sc.summary -> 'open' ->> 'critical')::int, 'limit', 0))
              ELSE '[]'::jsonb END
    FROM scans sc JOIN demo_repos d ON d.id = sc.repository_id
   WHERE sc.status = 'COMPLETED'
  ON CONFLICT DO NOTHING;

  -- Políticas de las organizaciones nuevas.
  INSERT INTO quality_gate_policies (id, organization_id, version, document, active, created_at)
  SELECT md5('demo2-policy-' || o.k)::uuid, o.id, 1,
         '{"version":1,"gates":[{"id":"max-critical","scope":"all_open","max":{"CRITICAL":0}},{"id":"no-new-high","scope":"new_findings","max":{"HIGH":0}}],"sla_days":{"CRITICAL":7,"HIGH":30,"MEDIUM":90},"fail_on_sla_breach":false}',
         true, v_now - interval '45 days'
    FROM demo_orgs o WHERE o.k > 1
  ON CONFLICT DO NOTHING;

  -- Entregas de webhooks de las últimas 24 h y contadores de rechazos y duplicados.
  INSERT INTO webhook_deliveries (delivery_id, event, received_at, outcome)
  SELECT md5('demo2-delivery-' || i)::uuid, CASE WHEN i % 3 = 0 THEN 'pull_request' ELSE 'push' END,
         v_now - make_interval(mins => i * 9 + i % 7),
         CASE WHEN i % 5 IN (0, 1, 2) THEN 'ENQUEUED' WHEN i % 17 = 0 THEN 'REJECTED' ELSE 'IGNORED' END
    FROM generate_series(1, 150) AS i
  ON CONFLICT DO NOTHING;
  INSERT INTO webhook_counters (minute, reason, count)
  SELECT date_trunc('minute', v_now - make_interval(hours => c.h, mins => c.m)), c.reason, c.n
    FROM (VALUES (1, 4, 'DUPLICATE', 2), (3, 12, 'DUPLICATE', 1), (5, 33, 'SIGNATURE', 14),
                 (5, 34, 'SIGNATURE', 9), (5, 35, 'SIGNATURE', 3), (9, 2, 'MALFORMED', 1),
                 (14, 50, 'MEDIA_TYPE', 2), (20, 8, 'DUPLICATE', 3)) AS c(h, m, reason, n)
  ON CONFLICT DO NOTHING;
END
$demo$;

COMMIT;
