# 0009 — Una sola tarea para obtener, escanear y procesar

- **Estado:** Aceptado (2026-10-11, criterio delegado por el responsable del proyecto: "lo más seguro y que menos problemas dé")
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §10

## Contexto

§10 preveía colas separadas `fetch`, `scan` y `report`. Al implementar los casos de uso de la fase 1 aparecen dos problemas si fetch y escaneo son tareas distintas:

1. **Vida del workspace.** El código clonado tendría que sobrevivir entre dos tareas, posiblemente en workers distintos. La limpieza dejaría de estar garantizada por un `finally` y dependería solo del barrido de huérfanos.
2. **Paso de resultados.** La salida de los escáneres tendría que viajar de una tarea a otra por Redis, que nunca debe guardar resultados (§4, §22), o por una tabla intermedia en PostgreSQL con datos aún sin validar.

## Opciones consideradas

1. **Tareas separadas con workspace compartido entre tareas.** Hace frágil la limpieza (AM-505) y obliga a almacenar resultados sin normalizar. Descartada.
2. **Tabla intermedia para resultados brutos.** Persiste datos no confiables antes de validarlos y contradice ADR 0006 (la salida bruta no se persiste). Descartada.
3. **Una tarea `EXECUTE` (cola `scan`) que obtiene, escanea y procesa.** El workspace vive dentro de un único `with` y los resultados pasan en memoria al normalizador. Elegida.

## Decisión

- `ScanStep.EXECUTE` en la cola `scan`: fetch → escáneres → `ProcessScanResults`. El workspace se libera **antes** de procesar (procesar solo toca la base de datos).
- `ScanStep.REPORT` en la cola `report`: reporte a GitHub, fuera de la transacción.
- Cola `maintenance` para jobs periódicos (ADR 0008).
- Desaparece la cola `fetch`.
- Ante una reentrega o un reintento, `ExecuteScan` retoma desde el fetch: el estado del escaneo no retrocede, y si ya estaba en PROCESSING se vuelve a escanear y se completa.

## Superficie de ataque y mitigaciones

- La limpieza del workspace queda garantizada por el gestor de contexto del fetcher, también ante errores y cancelaciones (lo verifican los tests de `ExecuteScan`).
- Los resultados nunca salen del proceso del worker antes de normalizarse: no pasan por Redis ni se escriben sin validar.

## Consecuencias

- §10 de CLAUDE.md pasa a listar las colas `scan`, `report` y `maintenance`.
- La concurrencia de escaneo se controla con un solo parámetro (workers de la cola `scan`), acotado además por el semáforo del runner (AM-404).
