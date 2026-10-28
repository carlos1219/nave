# 0030 — Administración de plataforma: usuarios, salud, webhooks y edición de la política

- **Estado:** Aceptado (2026-10-27; decisiones del responsable del proyecto)
- **Fecha:** 2026-10-27
- **Sección de CLAUDE.md afectada:** §12 (rol nuevo), §16 (salud), §9 (webhooks), §7 (política)

## Contexto

Todo en NAVE se autorizaba por organización. Faltaba un nivel por encima para lo que es de la instalación entera: quién usa NAVE, si sus servicios están vivos y qué entregas de GitHub llegan. Además, la política de quality gates solo se podía crear a mano en la base de datos.

## Decisión

### Rol de administrador de plataforma

- Tabla `platform_admins` (usuario y fecha de concesión). **Solo la CLI** lo concede o lo retira (`grant-platform-admin`, `revoke-platform-admin`), con entrada en el audit log. No hay ruta HTTP que la escriba; un test lo comprueba.
- Es independiente de los roles por organización: no da acceso a hallazgos ni escaneos de ninguna organización.
- Un usuario que no lo tiene recibe 404 en `/api/v1/platform/*`, como un no miembro (ADR 0026): el sondeo deja un aviso estructurado `nave.authz`, no una entrada de auditoría.
- *Trade-off:* la CLI usa la credencial `nave_app`, la misma que la API, porque el servicio `cli` ya funciona así para el primer ADMIN. Una API comprometida podría escribir la tabla; se mitiga con la auditoría y con que ninguna ruta lo haga. Separarlo exige otra credencial de base de datos.

### Gestor de usuarios

- Lista de usuarios (hasta 500, actividad más reciente primero) con sus organizaciones, roles y sesiones vivas.
- Cambiar o quitar el rol de un usuario en cualquier organización, con las mismas reglas que un ADMIN (nunca deja una organización sin ADMIN) y entrada `membership.changed` con `via=platform` en la auditoría de esa organización.
- **Nunca sobre sí mismo** (añadido en la fase 7, F7-31): sin esta regla un administrador de plataforma podía darse ADMIN en cualquier organización y leer sus datos, lo que contradice el punto anterior. El intento responde 403 y deja `access.denied` (`reason=self`) en la auditoría de esa organización. Riesgo residual: puede dar un rol a otra cuenta (RR-34).
- Cerrar todas las sesiones de un usuario (`session.revoked_by_platform`).

### Salud del servidor

- La API no tiene red hacia `scan-runner`. El `scheduler` encola cada minuto `nave.maintenance.health_check`. El worker comprueba la base de datos, el broker y la profundidad de cada cola, `scan-runner` (`GET /healthz`) y `egress-proxy` (conexión TCP), y guarda el resultado en `health_reports`, una fila por componente.
- La API añade en vivo la base de datos y el broker desde su propio lado. Si el latido tiene más de 3 minutos, worker y scheduler se marcan caídos: sin latido no se sabe nada de lo demás.

### Entregas de webhooks

- Las entregas válidas ya estaban en `webhook_deliveries` (30 días). Los duplicados y los rechazos (firma, tipo de contenido, cuerpo inválido) se **cuentan por minuto y motivo** en `webhook_counters`, sin cuerpo, cabeceras ni IP.
- Un rechazo por firma ocurre antes de autenticar: para que un atacante no pueda forzar escrituras, cada proceso de la API acumula en memoria y vuelca como mucho cada 30 s. Se pierden como mucho 30 s de recuentos si el proceso muere. La retención borra los contadores con el mismo plazo que las entregas.

### Edición de la política (ADMIN de la organización)

- `PUT /organizations/{id}/policy` crea una **versión nueva** activa y desactiva la anterior en la misma transacción; las versiones nunca se reescriben. Entrada `policy.changed` en la auditoría. La validación es la del dominio (`QualityGatePolicy`).

## Permisos de base de datos (migración 0007)

- `nave_app`: `SELECT, INSERT, DELETE` en `platform_admins`; `SELECT, INSERT, UPDATE` en `health_reports` y `webhook_counters`; `UPDATE (active)` en `quality_gate_policies`.
- `nave_maintenance`: `SELECT, DELETE` en `webhook_counters` (retención).

## Consecuencias

- Una tarea periódica más, cada minuto, y una fila por componente.
- La interfaz muestra la sección «Plataforma» solo si `GET /api/v1/me` dice que el usuario es administrador; la autorización real es la del servidor.
