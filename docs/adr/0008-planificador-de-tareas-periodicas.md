# 0008 — Planificador de tareas periódicas: servicio `scheduler`

- **Estado:** Aceptado (2026-10-11, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §10, §15, §21

## Contexto

ADR 0006 necesita jobs periódicos: purga por retención, barrido de workspaces huérfanos y caducidad de escaneos en cola. ADR 0002 necesita además el barrido de contenedores y volúmenes huérfanos del runner. Algo tiene que disparar esos jobs.

## Opciones consideradas

1. **`celery worker -B`** (beat embebido en el worker). Si se escala el worker se duplican las planificaciones, y un fallo de beat tumba al worker. La documentación de Celery lo desaconseja en producción. Descartada.
2. **Cron del host.** Queda fuera de Compose, no es portable a Docker Desktop sobre Windows y nadie lo ve al revisar el despliegue. Descartada.
3. **Servicio `scheduler` dedicado con `celery beat`.** Usa la misma imagen que el worker, solo publica mensajes con IDs en Redis y no ejecuta trabajo. Elegida.

## Decisión

- `scheduler` ejecuta `celery -A nave beat` con la planificación **definida en código**, no editable en runtime. El fichero de estado de beat vive en `tmpfs`.
- Red `internal` únicamente. Su único secreto es `redis_password`: sin credenciales de BD, sin la clave de la GitHub App y sin acceso al runner.
- Las tareas periódicas van a la cola `maintenance`, que procesa el worker con concurrencia propia.
- Todas las tareas periódicas son idempotentes: una ejecución perdida o duplicada (p. ej. al reiniciar beat) no causa daño.

## Superficie de ataque y mitigaciones

Superficie mínima: no recibe tráfico, no tiene puertos y solo escribe en Redis. Si se compromete, solo puede encolar tareas de mantenimiento. Esas tareas vuelven a validar todo contra PostgreSQL (AM-303), y su peor efecto es ejecutar antes de tiempo una purga que de todos modos respeta las ventanas de retención.

## Consecuencias

Añade el servicio de infraestructura `scheduler` y la cola `maintenance`.
