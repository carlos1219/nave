# 0029 — Barra lateral con iconos funcionales y vistas de triage: PR, cola, fallidos, escáneres, sesiones y exportación

- **Estado:** Aceptado (2026-10-27; petición del responsable del proyecto)
- **Fecha:** 2026-10-27
- **Sección de CLAUDE.md afectada:** §13 (iconos), §11 (endpoints), §12 (sesiones), §7 (resumen de escaneo)

## Contexto

Con ocho pestañas por organización la fila de navegación se desbordaba, y faltaban vistas de uso diario: los hallazgos que introduce una PR, una cola de triage de toda la organización, los escaneos que fallan, qué versión de cada escáner se usó, las sesiones abiertas del usuario y la descarga de tablas. El responsable pidió además un menú lateral colapsable «con algún símbolo descriptivo».

§13 prohíbe los iconos **decorativos**. Un icono de navegación que identifica una sección cuando el menú está contraído no decora: es la única marca visible de cada enlace.

## Decisión

### Navegación

- **Barra lateral** (`widgets/app-sidebar`) en lugar de las pestañas de `organization-shell`: un bloque general (Organizaciones, Mis sesiones) y, dentro de una organización, cuatro grupos: Panorama, Triage, Escaneos y Gobierno.
- **Colapsable** con un botón con `aria-expanded`. La preferencia vive en memoria durante la visita: el almacenamiento del navegador está vetado (AM-705), así que al recargar el menú vuelve a estar desplegado. Por debajo de 900 px el menú queda siempre contraído. Anchos en los tokens `--sidebar-w` y `--sidebar-w-collapsed`.
- **Iconos funcionales** (`shared/ui/NavIcon`): trazos monocromos en SVG propio con `currentColor`, `aria-hidden`, sin dependencias ni fuentes de iconos. Cada enlace conserva su texto: visible con el menú desplegado; con el menú contraído, como nombre accesible y en `title`. Fuera de la navegación sigue sin haber iconos.

### Vistas nuevas

| Endpoint (`/api/v1/…`) | Rol mínimo | Contenido |
|---|---|---|
| `organizations/{id}/pull-requests` | VIEWER | último escaneo de cada PR (hasta 200), con los recuentos de hallazgos nuevos |
| `organizations/{id}/triage-queue` | VIEWER | hallazgos `OPEN` de todos los repositorios por severidad y antigüedad; filtros de severidad, escáner y repositorio; paginado por cursor |
| `organizations/{id}/failed-scans` | VIEWER | escaneos `FAILED` y `TIMED_OUT` (hasta 200) con su causa, y si un escaneo posterior de la misma rama terminó bien |
| `organizations/{id}/scanners` | VIEWER | versiones de escáner usadas en los últimos 500 escaneos completados |
| `me/sessions` (GET, DELETE) y `me/sessions/{id}` (DELETE) | sesión | sesiones abiertas del usuario; cerrar una o todas las demás |
| `organizations/{id}/export` y `repositories/{id}/findings/export` | VIEWER | CSV o SARIF de la cola de triage, dependencias, riesgos aceptados o hallazgos de un repositorio (hasta 10 000 filas) |

- **Hallazgos de una PR:** no hay endpoint nuevo de hallazgos; la vista usa `GET /repositories/{id}/findings?seen_in_scan=…&only_new=true`, que ya existía (baseline por fingerprint, §7).
- **Relanzar un escaneo fallido** reutiliza `POST /repositories/{id}/scans` (TRIAGER) con el mismo commit y rama.
- **Versiones de escáner:** el resumen del escaneo (`ScanSummary.tools`) guarda escáner, versión, digest de la imagen y número de hallazgos de cada ejecución. Va en la columna JSONB `summary`: sin migración; los escaneos anteriores no tienen el dato.
- **Antigüedad de la base de datos de Trivy:** no se muestra. Solo la conoce el contenedor del escáner; exponerla exige cambiar el envoltorio `nave-scan` y el protocolo del runner (ADR 0012). Queda como trabajo aparte.

## Seguridad

- **Sesiones:** el identificador que ve el cliente es `SHA-256("nave-session-id" ‖ hash del token)` truncado a 32 hex: no permite reconstruir ni el token ni su hash. Solo se listan y cierran sesiones del propio usuario; la sesión actual no se cierra por este camino (se usa el logout). Cada cierre queda en el audit log (`session.revoked`).
- **Descarga en el navegador:** un enlace `<a download>` a la URL de la API (misma sesión por cookie, sin `fetch` ni `blob:`). Es el segundo componente con `:href` permitido por `tests/security/source.test.ts`, junto a la página de login: la URL la construye `shared/api` con UUID validados y valores de listas cerradas.
- **Exportación:** mismos filtros y autorización que las vistas. En CSV toda celda que empieza por `=`, `+`, `-`, `@`, tabulador o retorno de carro se prefija con `'` (inyección de fórmulas) y se eliminan los caracteres de control. El SARIF reutiliza el escritor de Code Scanning (texto en code spans, sin mensaje ni fragmento en secretos). `Content-Disposition: attachment` con nombre fijo construido por el servidor.
- **Cola de triage:** el cursor (`rango_microsegundos_uuid`) se valida con un patrón cerrado; los filtros de repositorio se restringen a la organización en la consulta.
- Los endpoints nuevos con `{id}` de organización están en `tests/security/test_tenant_isolation_api.py`.

## Consecuencias

- La barra lateral pide la lista de organizaciones para saber el rol (auditoría solo para ADMIN); es la misma petición que ya hace la cabecera de cada organización.
- `ScanSummary` crece con `tools`; el decodificador acepta resúmenes sin ese campo.
