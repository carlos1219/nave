# 0005 — Polling con backoff para el progreso de escaneos

- **Estado:** Aceptado (2026-10-11, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §14, §23

## Contexto

La UI muestra el progreso de cada escaneo y sus logs. §14 ya describe polling y deja SSE para una fase posterior.

## Opciones consideradas

1. **Polling con backoff.**
   - A favor: simple, sin conexiones largas, funciona con el rate limiting, compatible con workers síncronos de Flask.
   - En contra: algo de latencia y peticiones redundantes.
2. **SSE.**
   - A favor: tiempo real.
   - En contra: conexiones largas que agotan los workers síncronos (obligaría a gevent o async), configuración específica de Nginx (`proxy_buffering off`) y pub/sub en Redis.

## Decisión

Opción 1:

- `GET /api/v1/scans/{id}` devuelve estado, paso actual y `ETag`. El cliente envía `If-None-Match` y recibe `304` si nada cambió.
- Logs: `GET /api/v1/scans/{id}/logs?after=<seq>&limit=<n>`, paginados por cursor.
- Cliente: espera inicial de 1 s, se duplica en cada intento hasta un máximo de 15 s, con ±20 % de jitter. Se pausa con `document.hidden`, se para en estados terminales y cancela con `AbortController`.

## Consecuencias

Se revisa si la carga de polling deja de ser despreciable. SSE se añadiría con otro ADR sin cambiar el contrato de estados.
