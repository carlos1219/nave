# 0015 — API HTTP: configuración propia, verificación del webhook y errores uniformes

- **Estado:** Aceptado (2026-10-12; decisiones delegadas por el responsable del proyecto: «lo más seguro y que menos problemas dé»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §5.3, §9, §11, §12, §23
- **Desarrolla:** ADR 0008 (servicios), ADR 0014 (instalaciones)

## Contexto

La fase 4 añade el primer proceso que escucha en red: la API Flask y el endpoint de webhook de GitHub. Hay que fijar cómo se configura sin heredar secretos del worker, cómo se autentica y valida cada entrega, qué se responde a GitHub y cómo se formatean los errores.

## Opciones consideradas

1. **Una sola clase `Settings` para API y worker.** Simple, pero obliga a montar la clave de huella, la clave de la App y las credenciales del runner en el contenedor expuesto (A-01, AM-805).
2. **`ApiSettings` y `Settings` (worker) separadas, y la API rechaza arrancar si ve variables propias del worker.** Un fallo de despliegue que filtre secretos al proceso expuesto se detecta al arrancar.
3. Validar los payloads con código manual frente a Pydantic. Pydantic v2 ya figura en §3 para esquemas y da validación estricta y JSON Schema para OpenAPI (fase 4, C8).

## Decisión

- **Configuración:** opción 2. `ApiSettings` solo lee base de datos (`nave_app`), broker, secreto de webhook (actual y, opcionalmente, anterior para rotar sin corte), política de forks y límites. `WORKER_ONLY_VARIABLES` (clave de la App, huella, runner, mantenimiento, egress) en el entorno de la API es un error de arranque.
- **Dependencias nuevas** (versiones exactas, lockfile con hashes): `flask`, `gunicorn` (servidor WSGI de producción, sin servidor de desarrollo en la imagen) y `pydantic` v2 (esquemas, §3).
- **Verificación del webhook, en este orden y antes de cualquier trabajo:**
  1. Límite de cuerpo de 5 MiB aplicado por Flask (`MAX_CONTENT_LENGTH`), también con transferencia fragmentada.
  2. `X-Hub-Signature-256` con formato exacto `sha256=<64 hex>`; HMAC-SHA256 sobre el cuerpo **crudo** con `hmac.compare_digest` contra el secreto actual y el anterior. Cualquier fallo devuelve el mismo 401 sin detalle.
  3. Solo entonces: `Content-Type: application/json`, `X-GitHub-Delivery` (UUID canónico en minúsculas) y `X-GitHub-Event` en una lista cerrada.
  4. JSON estricto: se rechazan claves duplicadas (diferencial de parsers), profundidad excesiva y constantes `NaN`/`Infinity`.
- **Qué se toma del payload:** solo identificadores numéricos, SHA, ref y número de PR, con modelos Pydantic estrictos que ignoran el resto (AM-206). En `installation*` y `repository` solo el `installation.id`: se encola `sync_installation` (ADR 0014).
- **Respuestas a GitHub:** `202` cuando el trabajo quedó registrado o se ignora por diseño (evento desconocido, duplicado, repo no permitido, instalación que no coincide), de modo que GitHub no reintenta ni aprende nada del estado interno. `400` para un payload mal formado de un evento autenticado, `401` para firma inválida, `413`, `415`, y `503` si PostgreSQL o el broker no permiten registrar el trabajo (GitHub marca la entrega como fallida y se puede reenviar). Si el broker falla *después* de persistir el escaneo, `202`: el barrido de mantenimiento lo reencola (ADR 0011).
- **PR de forks:** `NAVE_SCAN_FORK_PULL_REQUESTS=off` por defecto (AM-205): un fork ejecuta código ajeno, aunque sea en el sandbox, y activarlo es una decisión explícita.
- **Errores:** RFC 7807 (`application/problem+json`) con `type` estable bajo `urn:nave:problem:`, sin trazas, rutas ni eco de la entrada. Un único manejador mapea `ApplicationError`/`DomainError` a HTTP; cualquier excepción no prevista es un 500 genérico con un `request_id` que se registra.
- **Salud:** `/healthz` (liveness, sin dependencias) y `/readyz` (PostgreSQL y Redis; responde solo `ok`/`unavailable`, nunca el motivo).
- **Cabeceras:** `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` y `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` en toda respuesta de la API; sin cabecera `Server` específica de Werkzeug/Gunicorn.

## Superficie de ataque y mitigaciones

AM-201 (falsificación), AM-202 (replay: idempotencia por delivery), AM-203 (cuerpos y ráfagas), AM-204/AM-809 (instalaciones), AM-205 (forks), AM-104 (divulgación) y AM-803/AM-805 (secretos del worker en la API). Los tests de seguridad cubren firma, límites, duplicados, claves duplicadas en JSON y divulgación de errores.

## Consecuencias

- La API ya no puede construirse con `Settings.from_environ`; el despliegue (fase 6) monta secretos distintos por servicio.
- `puertos-y-contratos.md` y el threat model se actualizan por componente.
- Autenticación de usuarios y CSRF (C7) y endpoints REST (C8) se añaden sobre esta misma fábrica de aplicación.
