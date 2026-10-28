# 0017 — API REST de negocio: contratos con Pydantic, OpenAPI versionado y errores de autorización

- **Estado:** Aceptado (2026-10-12; decisiones delegadas por el responsable del proyecto: «lo más seguro y que menos problemas dé»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §5.3, §11, §12, §23
- **Desarrolla:** ADR 0015 (API HTTP), ADR 0016 (sesiones y CSRF)

## Contexto

Con login y sesiones resueltos (ADR 0016), la fase 4 expone los casos de uso existentes por HTTP para la interfaz de la fase 5 y para integraciones: escaneos, findings, triage, miembros y organizaciones. Hay que decidir cómo se declaran los contratos, qué se devuelve ante un rol insuficiente y cómo se evita que el contrato derive del código.

## Opciones consideradas

1. **Documento OpenAPI escrito a mano** junto al código. Deriva sin que nadie lo note.
2. **Generar el contrato desde el propio código** (modelos Pydantic y un registro de operaciones) y versionar el resultado en `contracts/openapi.json` con un test que falle si difiere.
3. Una librería de «Flask + OpenAPI» (flask-smorest, spectree…). Dependencias nuevas y con su propio modelo de validación; el registro propio son ~150 líneas sobre Pydantic, que ya es dependencia (ADR 0015).

## Decisión

- **Opción 2.** Cada ruta se declara una vez con `Operation` (método, ruta, rol mínimo, modelo de entrada y de salida, parámetros). De esa declaración salen la ruta de Flask y el documento OpenAPI 3.1, servido en `GET /api/v1/openapi.json` (exige sesión). `contracts/openapi.json` está versionado; `tests/unit/delivery/test_openapi.py` falla si el documento servido difiere, si hay una ruta sin documentar, una referencia rota, o una enumeración distinta de la del dominio. Un cambio de contrato se regenera con `NAVE_UPDATE_CONTRACTS=1` y se revisa en el diff.
- **Entrada:** modelos Pydantic con `extra="forbid"` sobre JSON estricto (`loads_strict`: sin claves duplicadas, `NaN` ni profundidad excesiva), `Content-Type: application/json`, tope de 16 KiB. Parámetros de consulta: lista cerrada (un parámetro desconocido es 400), `limit` acotado, filtros repetibles con máximo de 8 valores. Identificadores solo en UUID canónico. El actor **siempre** es el de la sesión: un campo `actor` en el cuerpo es un error.
- **Salida:** modelos de respuesta explícitos; nunca fingerprints (derivados con la clave del worker), versiones de bloqueo optimista ni claves de idempotencia internas. Los textos de findings se devuelven como cadenas JSON y el contrato advierte que no son HTML.
- **Autorización:** exclusivamente en los casos de uso (`Authorizer`), no en las vistas. Una vista nueva sin caso de uso no puede decidir permisos.
- **Códigos:**
  - `404` si el recurso no existe **o** pertenece a una organización de la que el usuario no es miembro: la respuesta es byte a byte la misma (AM-106). Un test compara cada operación contra un id inexistente.
  - `403` si es miembro pero su rol no basta. Esto **cambia** el criterio de ADR 0015 (`AccessDeniedError` → 404): el `Authorizer` solo lo lanza a miembros, así que reconocer que el recurso existe no revela nada y la interfaz necesita distinguir «no tienes permiso» de «no existe». También es `403` un `Origin` o token CSRF incorrectos (ADR 0016).
  - `409` para conflictos de estado (último administrador, repositorio no escaneable, clave de idempotencia reutilizada con otro contenido), `503` con `Retry-After` para indisponibilidad transitoria.
- **Idempotencia:** `POST /repositories/{id}/scans` exige `Idempotency-Key` (UUID); repetirla devuelve `200` con `created: false` en lugar de `202`.
- **Paginación:** findings con cursor opaco por id; escaneos con cursor `(queued_at, id)` estrictamente decreciente, estable aunque lleguen escaneos nuevos entre páginas. Índice nuevo `ix_scans_repository_id_queued_at` (migración 0005).
- **Miembros:** se gestionan por id de usuario y el usuario debe haber iniciado sesión alguna vez (`NotFoundError` si no). No hay búsqueda de usuarios por correo: exponerla permitiría a cualquier administrador sondear qué personas tienen cuenta. Si la interfaz lo necesita se decide en la fase 5 con su propio análisis.
- **Sin CORS** y sin `Access-Control-*`: la interfaz se sirve desde el mismo origen (ADR 0016).

## Superficie de ataque y mitigaciones

AM-105 (cuerpos y parámetros acotados), AM-106 (aislamiento entre organizaciones), AM-107 (escalada de rol), AM-102 (CSRF), AM-104 (errores), AM-814 (el actor sale solo de la sesión). Tests: `tests/security/test_tenant_isolation_api.py`, `tests/unit/delivery/test_rest_api.py`, `tests/unit/delivery/test_openapi.py`.

## Consecuencias

- `ListScans`, `ListMembers`, `ListRepositories`, `ListMyOrganizations`, `GetFinding` y `GetScanVerdict` son casos de uso nuevos (solo lectura); `ManageMembership` ahora comprueba que el usuario exista.
- Sin límite de tasa por usuario en esta fase (herramienta interna, RR-08); se reabre con la exposición pública.
- Cualquier consumidor del contrato (frontend de la fase 5) puede generarse a partir de `contracts/openapi.json`.
