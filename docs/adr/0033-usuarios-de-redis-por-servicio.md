# 0033 — Un usuario de Redis por servicio

- **Estado:** Aceptado (2026-10-31; cierra RR-31 de la revisión de seguridad de la fase 7, F7-18)
- **Fecha:** 2026-10-31
- **Sección de CLAUDE.md afectada:** §15 (Redis con contraseña/ACL)
- **Complementa:** ADR 0008 (el secreto del planificador pasa a ser `redis_scheduler_password`)

## Contexto

API, worker y planificador compartían el usuario `default` de Redis (`+@all -@admin`, todas las claves y canales). Quien comprometiera la API podía leer, borrar o consumir cualquier cola, ejecutar Lua y enviar órdenes de control al worker. No se puede quitar el scripting a quien consume: el `Lock` de redis-py (mutex de kombu) libera con un script Lua (F7-18).

## Decisión

Un usuario por servicio, cada uno con su secreto (`redis_api_password`, `redis_worker_password`, `redis_scheduler_password`) y su propia `NAVE_REDIS_URL` (`redis://nave-<servicio>@redis:6379/0`). Las listas salen de observar con `MONITOR` un ciclo completo (escaneo, cancelación, webhook de instalación y tareas periódicas) y se verificaron con `ACL LOG` (0 denegaciones) y con pruebas negativas:

| Usuario | Comandos | Claves | Canales |
|---|---|---|---|
| `nave-api` | conexión + `LPUSH`, `LLEN`, `SADD`, `SMEMBERS`, `PUBLISH` | `scan*`, `report*`, `maintenance*`, `_kombu.binding.*` | `/0.celery.pidbox` (revocación) |
| `nave-scheduler` | conexión + `LPUSH`, `LLEN`, `SADD`, `SMEMBERS` | `maintenance*`, `_kombu.binding.*` | ninguno |
| `nave-worker` | `+@all -@admin -@dangerous`, sin `FUNCTION`/`FCALL` (conserva Lua) | colas, `_kombu.binding.*`, `unacked*`, `*pidbox*`, `celeryev.*` | `pidbox` y eventos `celeryev` |
| `health` | `PING` (healthcheck del contenedor, sin contraseña) | ninguna | ninguno |
| `default` | desactivado | — | — |

`PSUBSCRIBE` compara el patrón de forma literal con los permitidos, por eso `/0.celeryev/worker.*` se declara tal cual.

## Consecuencias

- Una API o un planificador comprometidos solo pueden encolar en sus colas: no consumen, no borran, no leen mensajes sin confirmar ni ejecutan Lua. El planificador tampoco puede difundir órdenes de control.
- Riesgo que queda: un worker comprometido controla las colas que consume (necesita hacerlo).
- Si una versión nueva de Celery o kombu usa otro comando o clave, el síntoma es `NoPermissionError` en el log del servicio: se diagnostica con `ACL LOG` desde un usuario administrativo temporal y se amplía la lista aquí y en `deploy/redis/entrypoint.sh`.
- Despliegues existentes: generar los tres secretos nuevos (`python deploy/scripts/init_secrets.py`) y retirar `redis_password`.
