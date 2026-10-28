# CLAUDE.md — NAVE: Nodo de Análisis y Verificación Estática

> Plataforma centralizada de escaneo estático (SAST/SCA) integrada con GitHub.

> Este archivo es la fuente de verdad del proyecto. Léelo completo antes de escribir
> código, proponer diseño o modificar cualquier archivo. Si una petición contradice
> este documento, **señálalo y pide confirmación** antes de continuar. Si una decisión
> cambia, se actualiza este archivo y se registra un ADR.

---

## 0. Identidad del proyecto

- **Nombre:** **NAVE** — *Nodo de Análisis y Verificación Estática*. Siempre en mayúsculas en UI, documentación y logos de texto.
- **Concepto:** un nodo central (API) que despacha workers asíncronos que analizan código no confiable. Apodo interno del servicio central: "Nave Nodriza".
- **Ámbito:** herramienta de **uso interno exclusivo** de la empresa; no se ofrece a terceros. Si esto cambiase, revisar antes las licencias de reglas y escáneres (threat model AM-905).
- **Convenciones de nombres derivadas:**

| Elemento | Nombre |
|---|---|
| Paquete Python | `nave` (ruta `backend/src/nave/`) |
| Nombre en `pyproject.toml` | `nave-backend` |
| Frontend (`package.json`) | `nave-frontend` |
| Claves de servicio en Compose | `frontend`, `api`, `worker`, `postgres`, `redis`, `scan-runner`, `egress-proxy`, `scheduler` (el DNS interno no cambia) |
| `container_name` / imágenes | `nave-frontend`, `nave-api`, `nave-worker`, `nave-postgres`, `nave-redis`, `nave-scan-runner`, `nave-egress-proxy`, `nave-scheduler` |
| App Celery | `nave` |
| Base de datos / usuario | `nave` / `nave_app` (sin privilegios de superusuario) |
| Prefijo de métricas | `nave_` (ej. `nave_scan_duration_seconds`) |
| Campo de log `service` | `nave-api`, `nave-worker` |
| Prefijo de variables de entorno | `NAVE_` |
| Variable de Python en imports | `from nave.domain ...`, `from nave.application ...` |

- **Identidad visual:** solo wordmark tipográfico `NAVE` en monospace, sin logo, sin icono, sin mascota (coherente con §13).

---

## 1. Rol esperado

Actúas simultáneamente como:

- **Principal Engineer / Arquitecto Senior**: decisiones justificadas, trade-offs explícitos, diseño por capas, SOLID y DRY sin sobreingeniería.
- **DevSecOps Lead Senior**: modelo de amenazas primero, defensa en profundidad, mínimo privilegio, cadena de suministro verificable.

Principio rector: **este sistema ejecuta herramientas sobre código NO confiable.** El propio escáner es superficie de ataque. Toda decisión se evalúa primero desde seguridad, después desde elegancia.

## 2. Reglas de trabajo (cómo debes comportarte)

1. **Diseño antes que código.** Para cualquier funcionalidad nueva: confirma supuestos, identifica riesgos, propón puertos/contratos y solo entonces implementa.
2. **Trabaja por fases y por componentes.** No generes el sistema entero en una sola respuesta. Entrega un componente completo, probado y revisable por turno.
3. **Pregunta lo mínimo imprescindible.** Si hay ambigüedad menor, asume lo más seguro, decláralo en una línea y avanza. Si afecta a seguridad o a la arquitectura, pregunta.
4. **Código de producción, no demos.** Manejo de errores, tipos estrictos, validación de entrada en cada frontera, logs estructurados, sin `TODO` silenciosos.
5. **Sin comentarios obvios.** Comenta solo el *porqué* no evidente (decisiones de seguridad, invariantes, trade-offs). Los docstrings describen contratos de puertos y casos de uso.
6. **Cada entrega incluye**: código, tests, y una nota breve de "superficie de ataque y mitigaciones" si toca ejecución, red, parsing o autenticación.
7. **Al terminar un componente crítico**, haz una pasada de revisión como atacante (ver §17) y repórtala.
8. **Respeta las restricciones de estilo visual (§13) sin excepciones**, incluso en prototipos.
9. **No añadas dependencias** sin justificarlas (mantenimiento, licencia, superficie de ataque). Preferir stdlib y librerías maduras.
10. **Idioma**: conversación y documentación en español; identificadores de código, commits y nombres de API en inglés.

## 3. Descripción del producto

NAVE es la plataforma centralizada que:

- Recibe eventos de GitHub (push, pull_request) y peticiones manuales de escaneo.
- Obtiene el código de forma segura y lo analiza con **Semgrep (SAST)** y **Trivy (SCA / secretos / IaC)**.
- Normaliza los resultados a un formato canónico (**SARIF 2.1.0**), los deduplica y los persiste.
- Evalúa **quality gates** como política y devuelve el resultado a GitHub (Checks API, SARIF, comentarios en PR).
- Ofrece una UI de lectura densa para triage de hallazgos y seguimiento de logs de escaneo.

Extensible: añadir un escáner nuevo (Gitleaks, Bandit, etc.) **no debe tocar `domain` ni `application`**.

## 4. Stack tecnológico (fijado)

| Capa | Tecnología |
|---|---|
| Lenguaje backend | Python 3.12, tipado estricto (mypy `--strict`) |
| API | Flask (app factory), Pydantic v2 para esquemas |
| Async | Celery + Redis (broker y caché; **nunca fuente de verdad**) |
| Persistencia | PostgreSQL + SQLAlchemy 2.x + Alembic |
| Escáneres | Semgrep, Trivy (versiones fijadas por digest) |
| Frontend | Vue 3 + TypeScript estricto, Composition API, Vite |
| Arquitectura frontend | Feature-Sliced Design |
| Proxy / estáticos | Nginx |
| Orquestación | Docker Compose (frontend, api, worker, postgres, redis, scan-runner, egress-proxy, scheduler) |
| Aislamiento de escaneos | Contenedor efímero por escaneo creado por `scan-runner` sobre motor rootless + gVisor en producción (ADR 0002) |
| OIDC | Cliente genérico por discovery con Authlib; Keycloak solo en el perfil `dev` (ADR 0004) |
| Calidad | ruff, mypy, pytest, import-linter, ESLint, vue-tsc, Vitest |

Servicios Compose: `frontend` (Nginx), `api`, `worker`, `postgres`, `redis`, más los componentes de infraestructura `scan-runner` (ADR 0002), `egress-proxy` (ADR 0007) y `scheduler` (ADR 0008). Contenedores e imágenes con prefijo `nave-`, ver §0.

## 5. Arquitectura del backend: Hexagonal / Clean

### 5.1 Capas y regla de dependencia

```
delivery  ──►  application  ──►  domain
infrastructure ─► application / domain  (implementa puertos)
```

- `domain`: entidades, value objects, reglas puras. **Cero imports** de Flask, Celery, SQLAlchemy, subprocess, requests.
- `application`: casos de uso y **puertos** (interfaces `Protocol`/ABC). Solo depende de `domain`.
- `infrastructure`: adaptadores concretos (Celery, Postgres, Redis, sandbox, GitHub, Semgrep/Trivy). Implementa los puertos.
- `delivery`: Flask (HTTP) y entrypoints Celery. Solo valida entrada, resuelve dependencias y llama a un caso de uso. **Sin lógica de negocio.**
- La regla se verifica automáticamente con **import-linter** en CI. Una violación rompe el build.

### 5.2 Puertos obligatorios (en `application/ports`)

- `ScannerPort` — ejecuta un escáner y devuelve resultados normalizados.
- `RepositoryFetcherPort` — obtiene el código fuente a un workspace aislado.
- `ScanRepository` / `FindingRepository` — persistencia.
- `ScanQueuePort` — encolado de trabajos (adaptador Celery).
- `EventPublisherPort` — eventos de dominio.
- `PolicyEvaluatorPort` — evaluación de quality gates.
- `VcsReporterPort` — reporte a GitHub (checks, SARIF, comentarios).
- `Clock`, `IdGenerator` — para tests deterministas.
- `UnitOfWork` — atomicidad entre repositorios y outbox.
- `MembershipRepository` — autorización por organización (§12, ADR 0004).
- `AuditLogPort` — auditoría inmutable (§12).
- `ScanLogRepository` — logs de escaneo con cursor (ADR 0005).
- `WebhookDeliveryRepository` — idempotencia por `X-GitHub-Delivery` (§9).
- `PolicyRepository` — versiones de la política de quality gates y evaluaciones por escaneo (ADR 0003).
- `TenantCatalog` — organizaciones y repositorios (allowlist, §9; ADR 0004).
- `ScannerRegistryPort` — escáneres habilitados (§5.4); su implementación es el `ScannerRegistry` de infraestructura.
- `OutboxStore`, `EventSinkPort`, `ScanSweepStore`, `RetentionStore` — trabajos periódicos: relay del outbox, recuperación de escaneos atascados y retención (ADR 0011).
- `InsightsQueries` — consultas agregadas de solo lectura por organización (estadísticas, actividad, reglas, riesgos, dependencias, política, auditoría; ADR 0028; PR, cola de triage, fallidos y escáneres, ADR 0029).
- `FindingExportPort` — CSV y SARIF de filas de hallazgos para descarga (ADR 0029).
- `PlatformQueries`, `PlatformAdminRepository`, `HealthStore`, `HealthProbesPort`, `WebhookStatsPort` — administración de plataforma (ADR 0030).

Contratos detallados en `docs/architecture/puertos-y-contratos.md`.

### 5.3 Inyección de dependencias

- **Composition root único** (`bootstrap/container.py`). Es el único lugar que conoce las implementaciones concretas.
- Flask: app factory `create_app(config)`; los controladores reciben casos de uso ya construidos.
- Celery: las tareas son **otro adaptador de entrada** (delivery), delgadas, y obtienen el caso de uso del mismo container.
- Prohibido: singletons globales, `import` de infraestructura dentro de application/domain, service locators dispersos.

### 5.4 Escáneres como plugins

- Registro de escáneres (`ScannerRegistry`) que mapea `ScannerKind -> ScannerPort`.
- Cada adaptador declara: binario, versión esperada, comando permitido, parser de salida, límites propios.
- Añadir un escáner = nuevo adaptador + registro. Principio Open/Closed.

## 6. Estructura de carpetas

```
.
├── CLAUDE.md
├── docs/
│   ├── adr/                      # Architecture Decision Records (NNNN-titulo.md)
│   ├── architecture/             # puertos y contratos, modelo de datos, despliegue
│   ├── threat-model.md
│   └── runbooks/
├── backend/
│   ├── pyproject.toml
│   ├── importlinter.ini
│   ├── alembic/
│   ├── src/nave/
│   │   ├── domain/
│   │   │   ├── entities/         # Scan, Finding, Repository, TriageDecision
│   │   │   ├── value_objects/    # Severity, ScanStatus, Fingerprint, CommitSha, RepoRef
│   │   │   ├── events.py
│   │   │   ├── policies.py       # reglas puras (transiciones de estado, etc.)
│   │   │   └── errors.py
│   │   ├── application/
│   │   │   ├── ports/
│   │   │   ├── use_cases/        # start_scan.py, process_scan_results.py, triage_finding.py, ...
│   │   │   ├── dto/
│   │   │   └── services/         # normalizador SARIF, fingerprinting, diff baseline
│   │   ├── infrastructure/
│   │   │   ├── scanners/         # semgrep_adapter.py, trivy_adapter.py, registry.py
│   │   │   ├── sandbox/          # ejecución aislada, path guard, límites
│   │   │   ├── vcs/              # github_app_client.py, webhook_verifier.py
│   │   │   ├── persistence/      # modelos SQLAlchemy, repositorios, unit of work
│   │   │   ├── queue/            # celery_app.py, celery_queue_adapter.py
│   │   │   ├── cache/            # redis
│   │   │   ├── policy/           # evaluador de quality gates
│   │   │   └── observability/    # logging estructurado, métricas, trazas
│   │   ├── delivery/
│   │   │   ├── http/             # blueprints, schemas, middlewares, error handlers
│   │   │   └── tasks/            # tareas Celery (adaptadores de entrada)
│   │   └── bootstrap/
│   │       ├── config.py         # settings tipados desde entorno/secretos
│   │       ├── container.py      # composition root
│   │       └── app_factory.py
│   └── tests/
│       ├── unit/                 # domain + application con fakes
│       ├── integration/          # adaptadores reales (Postgres, Redis)
│       ├── contract/             # contrato común para cada ScannerPort
│       ├── security/             # path traversal, symlinks, límites, HMAC
│       └── e2e/
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── app/                  # providers, router, estilos globales, tokens
│       ├── pages/                # composición de widgets por ruta
│       ├── widgets/              # bloques autónomos (ScanResultsTable, ScanLogViewer)
│       ├── features/             # acciones de usuario (start-scan, triage-finding)
│       ├── entities/             # scan, finding, repository (modelo + api + ui mínima)
│       └── shared/
│           ├── api/              # cliente tipado (generado desde OpenAPI)
│           ├── ui/               # átomos y moléculas: DataTable, Modal, Layout, Badge
│           ├── lib/              # utilidades puras
│           └── config/
├── runner/                       # scan-runner (ADR 0002): único componente con acceso al motor de contenedores
├── deploy/
│   ├── nginx/nginx.conf
│   └── scanner-sandbox/          # imágenes fetcher/semgrep/trivy (sin red en escaneo, mínimo); path guard en fetcher/pathguard.py
├── docker-compose.yml
└── .github/workflows/            # CI: lint, tests, import-linter, SAST propio, SBOM, firma
```

Regla FSD: una capa solo importa de capas inferiores (`app > pages > widgets > features > entities > shared`). Sin imports entre slices del mismo nivel.

## 7. Modelo de dominio y datos

- **Scan**: id, repo, commit, ref, trigger (webhook/manual), estado, timestamps, versiones de escáner usadas, resumen.
- **ScanStatus** (máquina de estados explícita en `domain`): `QUEUED → FETCHING → SCANNING → PROCESSING → COMPLETED | FAILED | CANCELLED | TIMED_OUT`. Transiciones inválidas lanzan error de dominio.
- **Finding**: regla, escáner, severidad normalizada, ubicación, mensaje, snippet, CWE/CVE, **fingerprint estable**, estado de triage.
- **Triage**: `OPEN | FALSE_POSITIVE | ACCEPTED_RISK (con justificación y caducidad obligatorias) | FIXED`. Auditado (quién, cuándo, por qué).
- **Formato canónico**: SARIF 2.1.0. Los adaptadores convierten la salida nativa; el resto del sistema solo conoce el modelo normalizado.
- **Baseline/diff**: comparación por fingerprint para reportar solo hallazgos nuevos en PRs.
- **Quality gates**: política declarativa (umbral por severidad, SLAs, excepciones). Se evalúa tras el procesamiento y produce un veredicto con motivos.
- PostgreSQL es la fuente de verdad. Migraciones solo con Alembic, revisables y reversibles.

## 8. Ejecución de escáneres y aislamiento (CRÍTICO)

Requisitos no negociables para `infrastructure/sandbox` y los adaptadores:

- `subprocess` siempre con **`shell=False`**, lista de argumentos construida por código, **allowlist** de flags. Jamás interpolar entrada del usuario en comandos.
- **Path guard** (vive en `deploy/scanner-sandbox/fetcher/pathguard.py`, ADR 0012): resolver con `Path.resolve(strict=True)` y verificar que el path final queda dentro del workspace raíz. Rechazar symlinks que escapen, nombres con `..`, rutas absolutas inesperadas, bytes nulos.
- Workspace **efímero por escaneo**, directorio con permisos `0700`, **limpieza garantizada** en `finally` (y barrido periódico de huérfanos).
- Ejecución en **contenedor efímero** por escaneo: sin red, FS raíz de solo lectura, usuario no root, `cap_drop: ALL`, `no-new-privileges`, perfil `seccomp`, límites de CPU, memoria, PIDs, tiempo de reloj y tamaño de salida.
- **Nunca montar `docker.sock`** en el worker ni en el contenedor del escáner. La orquestación la hace exclusivamente `scan-runner` (ADR 0002): red propia, peticiones firmadas con HMAC y formato cerrado, contenedores construidos desde plantillas fijas.
- Clonado: sin submódulos por defecto, `core.hooksPath` desactivado, sin ejecución de hooks, `--depth` limitado, sin seguir symlinks fuera del árbol, límites de tamaño y número de ficheros (anti zip bomb).
- **No usar configuración que venga del repo escaneado** (reglas Semgrep, `trivy.yaml`, `.semgrepignore` arbitrarios) salvo allowlist explícita. Las reglas las provee la plataforma.
- Trivy con base de datos desde **mirror/caché controlada**, sin descarga arbitraria en tiempo de escaneo.
- Binarios y versiones de escáner **fijados por digest/checksum**.
- La salida del escáner es entrada no confiable: parsear con esquema estricto, límite de tamaño, sin `eval`/`pickle`.

## 9. Integración con GitHub

- **GitHub App** (no PAT). Tokens de instalación de vida corta, permisos mínimos, claves privadas desde gestor de secretos.
- **Webhooks**: verificación **HMAC-SHA256** con comparación de tiempo constante; idempotencia por `X-GitHub-Delivery`; respuesta rápida (encolar y devolver 202).
- Salida: Checks API, subida de SARIF a Code Scanning, comentarios en PR (con deduplicación).
- Gestión de rate limits (backoff, respeto de `Retry-After`/cabeceras de límite).
- Repos y organizaciones permitidos mediante allowlist configurable.

## 10. Celery y fiabilidad

- `acks_late=True`, `reject_on_worker_lost=True`, reintentos con backoff exponencial y jitter solo para errores transitorios.
- **Tareas idempotentes** (reejecutar no duplica findings ni reportes).
- `soft_time_limit` y `time_limit` siempre definidos.
- Colas separadas: `scan` (obtener, escanear y procesar en una sola tarea, ADR 0009), `report` y `maintenance`, con concurrencia independiente.
- Tareas periódicas disparadas por `celery beat` en el servicio `scheduler`, con la planificación definida en código (ADR 0008).
- Cancelación de jobs soportada; dead-letter/estado `FAILED` con causa registrada.
- Tarea Celery = adaptador delgado: deserializa, llama al caso de uso, nada más.
- Serialización JSON únicamente (nunca pickle).

## 11. API (delivery HTTP)

- Prefijo `/api/v1`. Contrato descrito en **OpenAPI** (fuente para el cliente TS generado).
- Controladores: validar con Pydantic → llamar caso de uso → mapear a respuesta. Errores de dominio mapeados a HTTP en un único error handler.
- Respuestas de error uniformes (RFC 7807 `application/problem+json`), sin filtrar trazas ni rutas internas.
- Endpoints mínimos: crear escaneo, consultar estado/progreso, listar y filtrar findings paginados, triage de finding, health (`/healthz`, `/readyz`), webhook de GitHub.
- Rate limiting, límite de tamaño de body, paginación obligatoria en listados.

## 12. AuthN / AuthZ / Multi-tenancy

- Autenticación por **OIDC/SSO**; sesiones con cookies `HttpOnly`, `Secure`, `SameSite`; protección CSRF.
- **RBAC** (viewer, triager, admin) con autorización por organización/repositorio en cada caso de uso, no solo en el borde HTTP.
- **Administrador de plataforma** (ADR 0030): rol aparte para usuarios, salud y webhooks de toda la instalación; solo lo concede la CLI y no da acceso a datos de ninguna organización.
- **Auditoría** inmutable de acciones sensibles (triage, cambio de política, accesos denegados).
- Secretos solo mediante Docker secrets / gestor de secretos; **nunca** en imágenes, repositorio, logs ni variables de entorno en claro cuando se pueda evitar.

## 13. Estilo visual — Interfaz empresarial moderna (ADR 0023, ajustado por ADR 0027 y 0029)

Sustituye al antiguo «Brutalismo Técnico». Sobrio, claro y legible; sin aspecto de plantilla genérica.

### Prohibido

Emojis, iconos decorativos (solo se permiten los iconos funcionales de la barra lateral, monocromos y siempre con su texto; ADR 0029), ilustraciones, degradados, glassmorphism/desenfoques, animaciones decorativas, sombras fuera de los tokens, colores fuera de `tokens.css`, tipografías externas, Tailwind o clases utilitarias genéricas.

### Tokens (CSS custom properties en `app/styles/tokens.css`)

Superficies (`--bg`, `--surface`, `--surface-2`), líneas (`--line`), texto (`--text`, `--text-2`, `--text-3`), acento (`--accent*`), severidades (`--sev-*`, `--sev-*-bg`, `--sev-*-dot`), radios (`--radius`, `--radius-sm`, `--radius-pill`), sombras (`--shadow-sm`, `--shadow-md`), espaciado (`--space-*`), tipografía (`--font-ui`, `--font-mono`, `--fs-*`). Tema claro por defecto y oscuro con `prefers-color-scheme`. Cambiarlos requiere ADR.

### Reglas de composición

- Fondo gris muy claro, contenido en tarjetas blancas con borde de 1 px y radio de 6–8 px. Contraste WCAG AA mínimo en ambos temas (lo comprueba un test).
- Cabecera fija con wordmark `NAVE`; un único color de acento para acciones primarias, enlaces y foco.
- **Severidad nunca solo por color:** siempre etiqueta textual (`CRITICAL`, `HIGH`, ...) además del color.
- Datos técnicos (commits, rutas, reglas, logs) en monoespaciada con `font-variant-numeric: tabular-nums`.
- Foco visible (outline 2 px del color de foco).
- Logs y snippets en bloque monoespaciada, **renderizados como texto plano**.
- **CSS con clases semánticas BEM-like propias** (`.scan-table__row--critical`), en `<style scoped>` consumiendo los tokens.

## 14. Frontend: reglas de código

- Vue 3 `<script setup lang="ts">`, TypeScript `strict`, sin `any`.
- **Toda la lógica en composables** (`useScanPolling`, `useFindingsFilter`, ...); los componentes `.vue` solo presentan y emiten eventos.
- Estado global **solo si es estrictamente necesario** (sesión, preferencias). Por defecto, estado local y composables. Si hace falta store, Pinia, mínimo.
- Componentes base reutilizables (atoms/molecules) en `shared/ui`: `DataTable` (genérica y tipada), `Modal`, `PageLayout`, `SeverityBadge`, `LogViewer`.
- **Polling del estado del job**: backoff progresivo con tope, `AbortController` para cancelar, pausa con `document.hidden`, parada en estados terminales, manejo de errores y reintentos acotados, limpieza en `onScopeDispose`. (SSE puede sustituirlo en una fase posterior vía ADR.)
- Cliente API tipado generado desde OpenAPI; sin `fetch` ad hoc en componentes.
- Tablas con miles de filas: **virtualización** y ordenación/filtrado en servidor cuando aplique.
- **Seguridad**: los findings contienen texto controlado por un atacante. **Prohibido `v-html`** y `innerHTML`; siempre interpolación de texto. CSP estricta.
- Accesibilidad: semántica de tabla real, navegación por teclado, `aria-sort`, etiquetas.

## 15. Infraestructura y despliegue

### Docker Compose

- Servicios: `frontend`, `api`, `worker`, `postgres`, `redis`, `scan-runner`, `egress-proxy`, `scheduler`.
- Redes segmentadas: `edge` (frontend↔api), `internal` (api/worker/scheduler↔postgres/redis, `internal: true`), `runner` (worker↔scan-runner, `internal: true`) y `egress` (`internal: true`: worker, api y contenedores de fetch solo alcanzan `egress-proxy`, que es el único con salida a Internet por la red `public`; allowlist solo de GitHub, ADR 0007 y 0022).
- Topología completa y reparto de secretos en `docs/architecture/despliegue.md`.
- Todos los servicios: `read_only: true` (con `tmpfs` donde haga falta), `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, usuario no root, `healthcheck`, límites de recursos, `restart` definido.
- Imágenes con **digest fijado**, builds multi-stage, sin herramientas de build en runtime.
- Solo `frontend` expone puerto al host. `postgres` y `redis` jamás publican puertos. Redis con contraseña/ACL.

### Nginx

- Sirve los estáticos compilados de Vue (SPA fallback a `index.html`, con cache agresiva solo para assets con hash).
- **Reverse proxy** `/api/` → `api:PORT`, misma origen para evitar CORS (no habilitar CORS comodín).
- Cabeceras: CSP estricta, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `X-Frame-Options`/`frame-ancestors`, HSTS cuando haya TLS.
- `client_max_body_size` acotado, timeouts, `limit_req` en `/api/` y en el webhook, `server_tokens off`.

### Cadena de suministro y CI

- CI bloquea el merge si falla: ruff, mypy, pytest, import-linter, ESLint, vue-tsc, Vitest.
- La plataforma **se escanea a sí misma** (Semgrep + Trivy) en CI.
- SBOM propio (CycloneDX), imágenes firmadas (cosign), dependencias con lockfiles y actualización automatizada.

## 16. Observabilidad

- Logs **JSON estructurados** con `scan_id`, `request_id`, `delivery_id`. Nunca registrar tokens, secretos ni contenido del código escaneado.
- Métricas (Prometheus): duración de escaneos por escáner, cola, fallos, timeouts, hallazgos por severidad.
- Trazas OpenTelemetry a través de API → Celery → adaptadores.
- `/healthz` (liveness) y `/readyz` (dependencias).

## 17. Calidad, testing y revisión

- **Pirámide**: unit (domain/application con fakes, sin I/O) ≫ integration ≫ e2e.
- **Tests de contrato** compartidos para toda implementación de `ScannerPort`.
- **Tests de seguridad obligatorios**: path traversal, symlink escape, límites de tiempo/memoria/salida, verificación HMAC, idempotencia de webhook, inyección de argumentos, XSS en renderizado de findings.
- Cobertura objetivo: dominio y aplicación ≥ 90%; las rutas de seguridad, 100% de ramas relevantes.
- **Revisión como atacante** al cerrar cada componente crítico: listar vectores (entrada controlada, límites de confianza cruzados, escalada, DoS por recursos, fuga de secretos) y comprobar que existe mitigación y test.

## 18. Convenciones de código

- Python: `ruff` (lint + format), `mypy --strict`, tipos explícitos en puertos y casos de uso, `dataclass(frozen=True, slots=True)` para entidades y value objects cuando sea posible, excepciones de dominio específicas (no `Exception` genérica).
- Casos de uso: una clase por caso de uso, un método público `execute(command) -> result`, dependencias por constructor, sin estado mutable compartido.
- Errores: el dominio y la aplicación lanzan errores propios; la capa de delivery los traduce.
- Nada de lógica en `__init__.py`. Nada de `print`. Nada de `except:` desnudo ni silenciar excepciones.
- Frontend: ESLint + `vue-tsc`, nombres en `PascalCase` para componentes, `useXxx` para composables, barrel files (`index.ts`) solo como API pública de cada slice.
- Commits: Conventional Commits. Un cambio lógico por commit.

## 19. Definition of Done (por componente)

- [ ] Respeta la regla de dependencias (import-linter en verde).
- [ ] Tipos estrictos y linters sin errores.
- [ ] Tests unitarios y de seguridad relevantes incluidos y en verde.
- [ ] Entradas validadas en cada frontera; errores gestionados y mapeados.
- [ ] Sin secretos, sin `shell=True`, sin `v-html`, sin `pickle`.
- [ ] Logs estructurados sin datos sensibles.
- [ ] Cumple tokens de estilo (§13) si hay UI.
- [ ] Nota de superficie de ataque y mitigaciones.
- [ ] ADR creado o actualizado si se tomó una decisión estructural.

## 20. Fases de entrega

0. **Diseño**: threat model (STRIDE), supuestos, ADRs, puertos y contratos, modelo de datos, diagrama de despliegue.
1. **Dominio + aplicación**: entidades, máquina de estados, casos de uso, puertos, fakes y tests.
2. **Infraestructura de escaneo**: sandbox, path guard, adaptadores Semgrep/Trivy, normalización SARIF, tests de seguridad.
3. **Persistencia + Celery**: Postgres, repositorios, unit of work, tareas idempotentes, colas.
4. **API + GitHub**: controladores, webhooks HMAC, GitHub App, Checks/SARIF.
5. **Frontend**: shared/ui, entities, features, widgets (`ScanResultsTable`, log viewer), páginas, composable de polling.
6. **Despliegue**: Docker Compose endurecido, Nginx, CI/CD, firma y SBOM.
7. **Revisión final** de seguridad sobre el sistema completo.

## 21. No objetivos (no propongas ni implementes sin pedirlo)

DAST, Kubernetes, multi-cloud, soporte para GitLab/Bitbucket, aplicación móvil, tema oscuro/claro conmutable, microservicios adicionales de negocio (los componentes de infraestructura `scan-runner`, `egress-proxy` y `scheduler` están aprobados por los ADR 0002, 0007 y 0008), autoremediación automática de código.

## 22. Antipatrones prohibidos

- Lógica de negocio en controladores Flask o en tareas Celery.
- Importar infraestructura desde `domain` o `application`.
- Redis como almacenamiento de resultados o estado definitivo.
- `shell=True`, concatenar comandos, confiar en rutas o nombres de repositorio sin validar.
- Ejecutar escáneres directamente en el contenedor del worker sin aislamiento.
- Configuración o reglas de escaneo tomadas del repo analizado sin allowlist.
- Renderizar contenido de findings como HTML.
- Estado global en frontend "por comodidad".
- Estilos genéricos de IA (ver §13) o clases utilitarias tipo Tailwind.
- Dependencias sin fijar o imágenes sin digest.
- Respuestas de "esqueleto" con funciones vacías o `pass` en rutas críticas.

## 23. Decisiones de arquitectura

Resueltas en fase 0 (detalle en `docs/adr/`):

| Decisión | Resultado | ADR |
|---|---|---|
| Contenedor efímero por escaneo | Servicio `scan-runner` + motor rootless + gVisor | 0002 |
| Motor de políticas | Propio, declarativo, detrás de `PolicyEvaluatorPort` | 0003 |
| Autenticación y organizaciones | OIDC genérico (Authlib); organización = instalación de la GitHub App | 0004 |
| Progreso en vivo | Polling con backoff y ETag | 0005 |
| Retención | Workspaces nunca persistidos; tabla de plazos por tipo de dato | 0006 |
| Egress | `egress-proxy` con allowlist de dominios de GitHub | 0007 |
| Tareas periódicas | Servicio `scheduler` con `celery beat` | 0008 |
| Colas y tareas de escaneo | Una tarea `EXECUTE` (fetch + escaneo + procesado); el workspace no sobrevive a la tarea | 0009 |
| Entrega de tareas | Al menos una vez + casos de uso idempotentes; PostgreSQL decide, Redis transporta; sin backend de resultados | 0010 |
| Trabajos periódicos | Relay del outbox, barrido de escaneos atascados y retención; credencial `nave_maintenance` aparte | 0011 |
| Protocolo del runner | `POST /v1/runs` firmado con HMAC; token de clonado por stdin; workspace tmpfs | 0012 |
| Token de instalación de GitHub | Valor opaco (`ghs_<APPID>_<JWT>`, ~520 caracteres); patrón `[A-Za-z0-9._-]{20,1024}` | 0013 |
| Integración con la GitHub App | JWT RS256; tokens de un repo (`FETCH` revocado, `REPORT` en caché); instalaciones sincronizadas desde la API de GitHub; purga con función SQL `SECURITY DEFINER` | 0014 |
| API HTTP | `ApiSettings` propia (la API rechaza variables del worker); webhook con HMAC sobre cuerpo crudo antes de parsear, JSON estricto, 202 si el trabajo quedó registrado; RFC 7807 | 0015 |
| Login y sesiones | OIDC (code + PKCE, `joserfc`); sesión opaca en PostgreSQL (solo hash), cookie `__Host-`; CSRF = `Origin` exacto + token por sesión; primer admin solo por CLI | 0016 |
| API REST | Contratos Pydantic + registro de operaciones → OpenAPI versionado en `contracts/openapi.json` (test de deriva); autorización solo en casos de uso; 404 ajeno/inexistente idénticos, 403 rol insuficiente | 0017 |
| Límite de peticiones | Cubos de fichas en memoria: por usuario (lecturas/escrituras) y por IP solo en login; 429 con `Retry-After`; `X-Forwarded-For` solo con `NAVE_TRUSTED_PROXY_HOPS` | 0018 |
| Minimización de usuarios | Función SQL `nave_scrub_orphan_users` (solo `EXECUTE` para `nave_maintenance`): vacía nombre y correo de usuarios sin membresía ni sesión viva tras 30 días sin login; la fila se conserva | 0019 |
| Salida del login OIDC | `NAVE_OIDC_PROXY_URL` (solo API): túnel `CONNECT` con TLS de extremo a extremo; no se leen `HTTPS_PROXY`/`NO_PROXY` | 0020 |
| Arquitectura del frontend | Vue 3 + FSD con reglas de capas en ESLint; cliente generado desde `contracts/openapi.json`; CSRF solo en memoria; sin HTML dinámico ni estilos en línea; sin dependencias fuera del ADR | 0021 |
| Despliegue | Digests obligatorios vía `deploy/images.env`; Compose endurecido con redes segmentadas; Nginx sin privilegios con CSP y cabeceras en un fragmento único; CI con acciones fijadas por SHA, SBOM y firma | 0022 |
| Ficheros omitidos por tamaño | `nave-scan` (Semgrep) añade al SARIF un resultado por fichero mayor que `--max-target-bytes`; el adaptador marca la salida incompleta (veredicto NEUTRAL en vez de PASS) | 0024 |
| Workspace compartido entre fetch y escaneo | Un contenedor sostenedor sin red mantiene montado el volumen tmpfs del run; un escaneo solo se acepta si el sostenedor sigue vivo | 0025 |
| Auditoría de accesos denegados | Rol insuficiente de un miembro: audit log; sondeo de un no miembro (404): aviso estructurado `nave.authz`, nunca audit log | 0026 |
| Sistema visual de triage | Acento petróleo, fuentes solo del sistema, tablas densas con cabeceras en tipo de frase, ruta de navegación en cada página, `Panel` y `StatusTag` en `shared/ui` | 0027 |
| Vistas de organización | Puerto de lectura `InsightsQueries`; endpoints `stats`, `scans`, `accepted-risks`, `rules`, `dependencies`, `policy` (VIEWER) y `audit-log` (ADMIN); gráficas SVG propias | 0028 |
| Navegación y vistas de triage | Barra lateral colapsable con iconos funcionales; vistas de PR, cola de triage, escaneos fallidos, versiones de escáner (`ScanSummary.tools`), sesiones propias y exportación CSV/SARIF con neutralización de fórmulas | 0029 |
| Administración de plataforma | Rol `platform_admins` concedido solo por CLI; gestor de usuarios, salud por latido del worker en PostgreSQL, contadores de webhooks por minuto; edición versionada de la política por ADMIN | 0030 |

Abiertas: ninguna. Las nuevas se añaden aquí y se resuelven con un ADR antes de implementar.
