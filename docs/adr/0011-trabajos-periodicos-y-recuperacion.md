# 0011 — Trabajos periódicos: outbox, recuperación de escaneos atascados y retención

- **Estado:** Aceptado (2026-10-12, criterio delegado por el responsable del proyecto: "lo más seguro y que menos problemas dé")
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §10, §12
- **Desarrolla:** ADR 0006 (retención), ADR 0008 (scheduler)

## Contexto

ADR 0008 fija *quién* dispara los trabajos periódicos y ADR 0006 *cuánto* se conserva. Falta decidir *qué* hace cada trabajo, con qué credenciales y cómo se recupera el sistema de cada fallo.

## Decisión

**Tres tareas en la cola `maintenance`, planificación fija en código** (`infrastructure/queue/schedule.py`, sin variables de entorno ni edición en Redis):

| Tarea | Frecuencia | Qué hace |
|---|---|---|
| `relay_outbox` | cada 10 s | Entrega los eventos del outbox a los consumidores y los marca publicados, en una transacción. Entrega al menos una vez; `FOR UPDATE SKIP LOCKED` evita que dos relays se pisen |
| `sweep_scans` | cada 5 min | Recupera escaneos atascados (AM-308), ver abajo |
| `purge_expired_data` | diaria, 03:27 UTC | Retención por lotes (ADR 0006) |

Cada ejecución lleva `expires`: si nadie la atiende, se descarta en lugar de acumularse. No reintentan por sí mismas; la siguiente ejecución programada es el reintento.

**Recuperación de escaneos atascados.**
- En `QUEUED` más de 10 min: se reencola **una sola vez** (columna `scans.requeued_at`, migración 0002). Si está llena la cola, se pospone al siguiente barrido.
- En `QUEUED` más de 60 min: expira con `QUEUE_EXPIRED` y se reporta a GitHub.
- En curso más de `2 × límite duro de la tarea`: se abandona (`TIMEOUT`; `INTERNAL_ERROR` si estaba en `PROCESSING`). También acota el bucle de un mensaje venenoso que mata al worker en cada reentrega.

**Credenciales separadas.** Los borrados por retención usan el rol `nave_maintenance` (único con `DELETE` sobre las tablas con retención; sin acceso a `audit_log`, `triage_decisions`, `findings` ni `repositories`). El worker recibe su contraseña como un segundo secreto y construye un motor aparte; el resto de tareas usa `nave_app`. El servicio `scheduler` no recibe ninguna credencial de base de datos.

**Qué se conserva siempre al purgar escaneos:** los que aún referencia un finding (`first_seen_scan_id` o `last_seen_scan_id`), los que sustituyen a otro (`superseded_by`) y el último `COMPLETED` de la rama por defecto de cada repositorio (baseline).

**Mínimos de retención** (validados al arrancar): 1 día para `webhook_deliveries`, `scan_logs` y outbox publicado (por debajo se rompería la idempotencia de webhooks o el triage de un fallo reciente); 30 días para escaneos. Outbox publicado: 7 días por defecto.

**Consumidores del outbox.** Hoy solo `LogEventSink` (un log JSON por evento, con valores validados). Un consumidor nuevo debe ser idempotente, rápido y no lanzar por un evento malformado: se ejecuta dentro de la transacción del relay.

**Beat en memoria** (`celery.beat:Scheduler`): sin fichero de estado (`shelve` usa pickle). Al reiniciar, las tareas idempotentes simplemente se vuelven a lanzar.

## Alternativas descartadas

- *Un segundo servicio `worker-maintenance`:* aislaría la credencial de borrado, pero añade un servicio que §21 no contempla. Se reabre si la credencial de mantenimiento preocupa más que el servicio extra (RR-10).
- *Dar `DELETE` a `nave_app`:* rompería AM-305.
- *Reencolar de forma estática dentro de una ventana de tiempo (sin columna):* depende de que los barridos no se salten ni se retrasen.

## Fuera de alcance, con motivo

- **Purga de organizaciones desinstaladas (30 días de gracia, ADR 0006):** hoy nada marca una organización como `UNINSTALLED`; el manejo de eventos `installation` llega con la GitHub App (fase 4). Se implementará allí, junto a `uninstalled_at` y los permisos de borrado que necesita.
- **Barrido de workspaces/contenedores huérfanos y actualización de la base de datos de Trivy:** viven en `scan-runner` (fase 2), que es quien los crea.

## Superficie de ataque y mitigaciones

Ver AM-308, AM-309 y RR-10/RR-11 en `docs/threat-model.md`. Tests: `tests/integration/persistence/test_maintenance_stores.py` (incluye privilegios por rol), `tests/unit/application/test_maintenance.py`, `tests/security/test_scheduler.py`.
