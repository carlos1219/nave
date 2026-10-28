# 0010 — Semántica de entrega de tareas Celery

- **Estado:** Aceptado (2026-10-12, criterio delegado por el responsable del proyecto: "lo más seguro y que menos problemas dé")
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §10

## Contexto

Celery sobre Redis ofrece entrega *al menos una vez*, no *exactamente una*. Con `acks_late` una tarea cuyo worker muere se reentrega; con Redis, además, un mensaje no confirmado se reentrega al vencer `visibility_timeout`. Hay que decidir qué garantías damos y dónde vive la verdad.

## Decisión

1. **PostgreSQL decide; Redis transporta.** El mensaje lleva solo el ID del escaneo (AM-303). No hay backend de resultados (`result_backend=None`, `task_ignore_result`).
2. **Al menos una vez + casos de uso idempotentes.** El ID de tarea es determinista (`{scan_id}:{paso}`), pero el broker no deduplica: la seguridad ante duplicados la dan los casos de uso (`ExecuteScan` omite escaneos terminales y retoma los intermedios; `ReportToVcs` es idempotente). Un duplicado cuesta CPU, no corrompe datos.
3. **`visibility_timeout` calculado, no configurado:** límite duro más largo + mayor espera de reintento + 10 min. Así una tarea sana nunca se reentrega mientras sigue en marcha.
4. **Reintentos solo ante `TransientError` y `ConcurrentModificationError`** (un conflicto de versión es transitorio: repetir relee el estado). Backoff exponencial con jitter, `Retry-After` respetado hasta un tope. Al agotarse, `EXECUTE` abandona el escaneo (`FAILED`, `INTERNAL_ERROR`) y `REPORT` registra el error (el resultado ya está persistido).
5. **`soft_time_limit` → abandono con `TIMEOUT`;** `time_limit` mata al hijo y el barrido de mantenimiento cierra lo que quede a medias.
6. **`revoke` es de mejor esfuerzo y sin `terminate`:** matar al worker dejaría el workspace sin limpiar. Una tarea en curso se detiene al releer un estado terminal.
7. **Cola con tope** (`max_queue_depth`): si está llena, `enqueue` lanza `QueueUnavailableError` y el escaneo queda en `QUEUED` para que el barrido lo reencole (AM-308).
8. **Tareas con `shared=False` y `set_as_current=False`:** sin estado global de Celery (§5.3).

## Alternativas descartadas

- *Deduplicar en Redis con `SET NX`:* añade estado en Redis que puede quedar desfasado respecto a PostgreSQL y bloquear reencolados legítimos.
- *Bloqueo asesor en PostgreSQL por escaneo durante toda la tarea:* mantiene una conexión por tarea; se reconsidera si los duplicados resultan costosos en producción.
- *Firmar mensajes (`auth` serializer):* exige gestión de certificados; con ACL de Redis, red `internal` y solo JSON el beneficio no compensa.

## Superficie de ataque y mitigaciones

Ver AM-303, AM-304, AM-307 y RR-07/RR-08 en `docs/threat-model.md`.

## Consecuencias

- El worker debe lanzarse con pool `prefork`: los límites de tiempo no se aplican con `solo` ni con hilos (fase 6).
- Nuevas dependencias: `celery` y `redis` (ver `backend/README.md`).
