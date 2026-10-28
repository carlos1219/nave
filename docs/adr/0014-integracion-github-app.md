# 0014 — Integración con la GitHub App: tokens, reporte, instalaciones y purga

- **Estado:** Aceptado (2026-10-12, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §5.2, §9, §12, §23
- **Desarrolla:** ADR 0004 (organización = instalación), ADR 0006 (retención), ADR 0011 (RR-11)

## Contexto

La fase 4 conecta NAVE con GitHub. Hay cuatro decisiones que no están fijadas: cómo se autentica y se piden tokens, cómo se reporta, cómo se registran organizaciones y repositorios (hoy nada los puebla), y cómo se purgan las organizaciones desinstaladas sin ampliar el privilegio del worker (RR-10).

## Decisión

**Autenticación y tokens.**
- JWT RS256 de la App (`iat` 60 s en el pasado, `exp` ≤ 9 min) construido por NAVE y firmado con `cryptography`. La verificación de JWT no se implementa: GitHub los verifica. La clave privada solo la tiene el `worker` (A-01, AM-803).
- Cliente HTTP con la biblioteca estándar, como el del runner: sin redirecciones, respuesta acotada, TLS verificado, salida por `egress-proxy` (ADR 0007).
- Errores de GitHub: 403/429 con `Retry-After` o `x-ratelimit-reset` se traducen a `TransientError(retry_after)`; los 5xx, a transitorios; el resto, a errores permanentes sin eco del cuerpo remoto.
- Dos perfiles de token, siempre limitados a **un repositorio**: `FETCH` (`contents:read`, sin caché, **revocado** con `DELETE /installation/token` en cuanto termina el clonado; cumple AM-506) y `REPORT` (`checks:write`, `pull_requests:write`, `security_events:write`, con caché en memoria y margen de 5 min antes de caducar).

**Reporte.**
- Check run idempotente: se busca por `external_id = <scan_id>` y se crea o se actualiza. Conclusiones: PASS → `success`, FAIL → `failure`, NEUTRAL → `neutral`, FAILED → `failure`, TIMED_OUT → `timed_out`.
- Comentario de PR: solo se edita un comentario cuyo autor es la propia App; el marcador no basta (un atacante puede copiarlo en un comentario suyo). El cuerpo llega ya escapado (AM-802).
- Subida de SARIF a Code Scanning: GitHub solo la admite en repos públicos o en privados/internos con Code Security activo, y limita el fichero a 10 MB. Variable `NAVE_SARIF_UPLOAD=off|on`, **`off` por defecto**. Con `on`, si GitHub responde que Code Scanning no está disponible para el repo, se registra una advertencia y se continúa (el check y el comentario no dependen de esto). Si el SARIF pasa del tope, se recorta por severidad y el check lo indica.

**Instalaciones.**
- El payload de `installation`, `installation_repositories` y `repository` **no es la fuente de verdad**. El webhook solo encola `sync_installation(installation_id)` (únicamente el ID, AM-303). El worker consulta a GitHub (`GET /app/installations/{id}`; un 404 significa desinstalada; `GET /installation/repositories`, que incluye `default_branch`) y hace upserts idempotentes. Tolera eventos desordenados o perdidos (GitHub no reintenta entregas).
- Un job diario `reconcile_installations` (`GET /app/installations`) repara lo que se haya perdido y marca como `UNINSTALLED` las que ya no existen.
- **Allowlist de cuentas** `NAVE_GITHUB_ALLOWED_ACCOUNTS`, obligatoria: si está vacía no se registra ninguna instalación. Una instalación de una cuenta fuera de la lista se ignora y se audita.
- Los repositorios que concede la instalación se registran **habilitados**: la selección de repositorios de GitHub al instalar la App actúa como allowlist, junto con la de cuentas (confirmado por el responsable del proyecto).
- Una reinstalación genera un `installation_id` nuevo. Dentro del periodo de gracia se reactiva la organización existente identificándola por `github_account_id` (inmutable), no por login, y conserva repositorios, pertenencias y triage.
- `suspend` → `SUSPENDED`; `unsuspend` → `ACTIVE`.

**Purga de organizaciones desinstaladas (RR-11).**
- Migración 0003: `organizations.uninstalled_at`, `organizations.github_account_id`, y `UPDATE` sobre `organizations` y `repositories` para `nave_app`.
- Función SQL `nave_purge_organization(org_id)`, `SECURITY DEFINER`, que comprueba en SQL que la organización está `UNINSTALLED` desde hace **más de 30 días** (plazo fijo en la función, no un argumento) y borra en orden de FK todo salvo `audit_log`, que no referencia organizaciones por FK. `nave_maintenance` recibe solo `EXECUTE` sobre ella: no obtiene `DELETE` sobre `findings`, `triage_decisions` ni `repositories`.
- El caso de uso `PurgeUninstalledOrganizations` (tarea diaria en `maintenance`) audita `organization.purge_requested` con `nave_app` antes de llamar a la función y `organization.purged` después. Si cae a medias, la siguiente ejecución repite sin daño.

## Alternativas descartadas

- *Confiar en el payload de los webhooks de instalación:* depende del orden y de que no se pierdan entregas, y registraría repos sin `default_branch`.
- *Dar `DELETE` directo a `nave_maintenance` sobre las tablas de negocio:* amplía RR-10; un worker comprometido podría borrar findings y repositorios de organizaciones activas.
- *Segundo servicio `worker-maintenance`:* aísla la credencial, pero añade un servicio que §21 no contempla; se reabre solo si la función no basta.
- *Repos deshabilitados hasta que un admin los habilite:* más estricto, pero exige un endpoint de administración y fricción por repo.

## Superficie de ataque y mitigaciones

AM-206, AM-506, AM-802, AM-803, AM-804, RR-10 y RR-11. Se añadirán filas al threat model en cada componente (instalación cruzada de cuentas, suplantación de comentario, privilegio de la purga). Nuevas dependencias: `cryptography` (la exige también Authlib, ADR 0004).

## Consecuencias

- Nuevos puertos: `InstallationSyncQueuePort`, escritura en `TenantCatalog` (o puerto propio), `InstallationDirectoryPort` (consulta a GitHub), `OrganizationPurgeStore`.
- Nuevas tareas en `maintenance`: `sync_installation`, `reconcile_installations`, `purge_uninstalled_organizations`; la planificación sigue fija en código (ADR 0008).
- La API (sin la clave de la App) no habla con GitHub salvo para recibir webhooks.
- `puertos-y-contratos.md` se corrige: `VcsReporterPort` tiene `publish_check`, `upload_sarif` y `upsert_pr_comment`.
