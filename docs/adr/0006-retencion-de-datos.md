# 0006 — Retención de findings, logs y workspaces

- **Estado:** Aceptado (2026-10-11, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §7, §16, §23

## Contexto

El código escaneado y los findings son sensibles (A-04, A-05). Conservar de más aumenta el impacto de una brecha; conservar de menos rompe el baseline, el triage y la auditoría.

## Decisión

Valores por defecto, configurables con `NAVE_RETENTION_*`:

| Dato | Retención | Notas |
|---|---|---|
| Workspace (código clonado) | Fin del paso | Se borra en `finally`. Barrido cada 10 min de volúmenes con antigüedad mayor que `time_limit` + 5 min. **Nunca se persiste.** |
| Salida bruta del escáner | No se persiste | Solo se guardan el SARIF normalizado y su hash |
| SARIF normalizado por escaneo | 90 días | |
| `scans` y `finding_occurrences` | 365 días | Siempre se conserva el último escaneo completado de la rama por defecto (baseline) |
| `findings` y `triage_decisions` | Mientras exista el repositorio | `triage_decisions` es append-only |
| `audit_log` | Mínimo 2 años | Append-only; `nave_app` no tiene `DELETE` |
| `scan_logs` | 30 días | |
| `webhook_deliveries` | 30 días | Ventana de idempotencia (RR-03) |
| Datos de un repo desinstalado | 30 días de gracia y purga | Excepto `audit_log` |
| Logs de aplicación (stdout JSON) | Fuera de NAVE | Se recomiendan 30 días en el colector |

## Superficie de ataque y mitigaciones

Una retención menor reduce el impacto de una brecha. El borrado lo hace un job con rol propio (`nave_maintenance`), sin acceso a `audit_log`.

## Consecuencias

- Los jobs periódicos (purga y barrido de huérfanos) los dispara el servicio `scheduler` (ADR 0008).
- Se añade una cola `maintenance` a las de §10.
