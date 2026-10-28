# 0021 — Arquitectura del frontend: Vue 3, Feature-Sliced, cliente generado y sesión por cookie

- **Estado:** Aceptado (2026-10-12; decisiones delegadas por el responsable del proyecto: «lo más seguro y que menos problemas dé»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §4, §13, §14, §15, §20
- **Desarrolla:** ADR 0016 (sesión y CSRF), ADR 0017 (API REST)

## Contexto

La fase 5 construye la interfaz sobre la API de la fase 4. Los findings contienen texto controlado por un atacante (rutas, mensajes, fragmentos de código), así que el frontend es una frontera de confianza (TB7). Hay que fijar herramientas, cómo se consume el contrato, cómo se maneja la sesión y cómo se evita el XSS por construcción.

## Decisión

**Herramientas (versiones exactas en `package.json`, `package-lock.json` y `npm ci`)**
Vue 3.5, Vite, TypeScript 5.9 (la única serie que admiten a la vez `openapi-typescript`, `typescript-eslint` y `vue-tsc`), `vue-router`, Pinia (solo para la sesión), Vitest con `@vue/test-utils` y `jsdom`, ESLint con `eslint-plugin-vue` y `typescript-eslint`, y `openapi-typescript` para los tipos del contrato. Nada más sin ADR: sin librería de componentes, sin Tailwind, sin librería de fechas, sin iconos, sin virtualizador. TypeScript `strict`, `noUncheckedIndexedAccess` y `exactOptionalPropertyTypes`; `any` prohibido por ESLint.

**Estructura:** Feature-Sliced Design (`app`, `pages`, `widgets`, `features`, `entities`, `shared`). Una capa solo importa de las inferiores; cada slice expone su API pública por `index.ts`. Lo comprueba una regla de ESLint (`no-restricted-imports` por capa) en CI, el equivalente de import-linter.

**Contrato:** los tipos se generan desde `contracts/openapi.json` (la misma fuente versionada del backend) a `shared/api/schema.d.ts`, que se versiona. Un test de deriva regenera en memoria y falla si difiere. Un único cliente (`shared/api/client.ts`) envuelve `fetch`: misma origen, `credentials: "same-origin"`, `X-CSRF-Token` en métodos no seguros, cuerpo JSON, `AbortSignal` obligatorio en lecturas con polling, y traducción de `application/problem+json` a un `ApiError` tipado (`status`, `type`, `request_id`, `retryAfter`). Ningún componente llama a `fetch`.

**Sesión:** cookie `HttpOnly` que JavaScript no ve. Al arrancar se pide `GET /session`; el token CSRF vive solo en memoria (Pinia), nunca en `localStorage`. Un 401 en cualquier llamada vacía la sesión y lleva a la pantalla de entrada; un 403 se muestra como falta de permiso; un 429 respeta `Retry-After`. El login es una navegación completa a `/api/v1/auth/login` (nunca `fetch`), y no se acepta URL de retorno.

**Datos del atacante:** solo interpolación de texto. `v-html`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` y `eval` están prohibidos por ESLint (error) y por un test que recorre el código fuente. Los enlaces construidos con datos (rutas a GitHub) se arman a partir de campos validados, nunca con una URL que venga tal cual del servidor. Logs y fragmentos en `<pre>` como texto, con recorte de longitud en el cliente.

**CSP y estáticos:** sin scripts ni estilos en línea, sin `eval`, sin fuentes ni imágenes externas: la CSP de Nginx (fase 6) será `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`. Vite se configura sin preload inline ni `<style>` inyectado en línea. Por eso los estilos `scoped` se extraen a ficheros CSS en el build. Las fuentes del §13 se declaran como pila con retroceso a monoespaciada del sistema; empaquetarlas es una decisión posterior.

**Listados:** paginación por cursor del servidor y ordenación y filtros en servidor. Sin virtualización: las páginas son de tamaño acotado y se acumulan con tope; si una vista necesitara miles de filas simultáneas se decidirá en otro ADR.

**Polling:** un único composable (`useScanPolling`) con backoff progresivo con tope, `AbortController`, pausa con `document.hidden`, parada en estados terminales, reintentos acotados y limpieza en `onScopeDispose`.

**Estado:** local y por composables. Pinia solo para la sesión.

**Visual:** los tokens de `app/styles/tokens.css` no cambian. Un test recorre los CSS y falla ante `border-radius` distinto de 0, `box-shadow`, `drop-shadow`, gradientes, `@keyframes` o `transition` decorativa, y colores fuera de los tokens.

## Plan de componentes (uno por turno, cada uno con tests y nota de ataque)

1. **F0 Cimientos:** `package.json`, lockfile, Vite, TypeScript, ESLint con las reglas de capas y de HTML, Vitest, tokens y base CSS, tests de estilo y de código fuente.
2. **F1 Cliente API:** tipos generados, cliente, `ApiError`, test de deriva.
3. **F2 Sesión y router:** store, guardia de rutas, pantalla de entrada, cierre de sesión.
4. **F3 `shared/ui`:** `PageLayout`, `DataTable`, `Modal`, `SeverityBadge`, `LogViewer`.
5. **F4 Entidades y features de lectura:** organizaciones, repositorios, escaneos, findings, filtros.
6. **F5 Features de escritura:** iniciar y cancelar escaneo, triage, gestión de miembros.
7. **F6 Widgets y páginas:** `ScanResultsTable`, visor de logs, veredicto del quality gate, páginas.
8. **F7 Revisión como atacante:** XSS con findings hostiles, CSP, abuso de sesión.

## Superficie de ataque y mitigaciones

Ver AM-701 a AM-706 (XSS y enlaces por contenido de findings, robo del token CSRF, redirección abierta tras el login), AM-101 y AM-102 (sesión y CSRF) y AM-904 (dependencias de frontend comprometidas).

## Consecuencias

- Un segundo árbol de dependencias con su lockfile y su CI (ESLint, `vue-tsc`, Vitest).
- Fuentes sin empaquetar al principio: el aspecto final depende de las instaladas en el equipo del usuario hasta que se decida.
- Cualquier dependencia nueva exige justificarla aquí o en un ADR propio.
