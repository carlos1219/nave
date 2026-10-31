# Índice de ADRs

| Nº | Título | Estado |
|---|---|---|
| 0000 | [Plantilla](0000-plantilla.md) | — |
| 0001 | [Registrar las decisiones de arquitectura con ADRs](0001-registrar-decisiones-de-arquitectura.md) | Aceptado |
| 0002 | [Ejecución aislada por escaneo mediante `scan-runner`](0002-aislamiento-por-escaneo-scan-runner.md) | Aceptado |
| 0003 | [Motor de políticas propio](0003-motor-de-politicas.md) | Aceptado |
| 0004 | [Cliente OIDC genérico y modelo de organizaciones](0004-oidc-y-modelo-de-organizaciones.md) | Aceptado |
| 0005 | [Polling con backoff](0005-polling-con-backoff.md) | Aceptado |
| 0006 | [Retención de datos](0006-retencion-de-datos.md) | Aceptado |
| 0007 | [Egress controlado hacia GitHub](0007-egress-controlado.md) | Aceptado |
| 0008 | [Planificador de tareas periódicas](0008-planificador-de-tareas-periodicas.md) | Aceptado |
| 0009 | [Una sola tarea para obtener, escanear y procesar](0009-tarea-unica-de-ejecucion.md) | Aceptado |
| 0010 | [Semántica de entrega de tareas Celery](0010-semantica-de-entrega-de-tareas.md) | Aceptado |
| 0011 | [Trabajos periódicos y recuperación](0011-trabajos-periodicos-y-recuperacion.md) | Aceptado |
| 0012 | [Protocolo del runner, token por stdin y workspace](0012-protocolo-del-runner.md) | Aceptado |
| 0013 | [El token de instalación de GitHub es un valor opaco](0013-token-de-instalacion-opaco.md) | Aceptado |
| 0014 | [Integración con la GitHub App: tokens, reporte, instalaciones y purga](0014-integracion-github-app.md) | Aceptado |
| 0015 | [API HTTP: configuración propia, verificación del webhook y errores uniformes](0015-api-http-y-webhook.md) | Aceptado |
| 0016 | [Login OIDC, sesiones en PostgreSQL, CSRF y primer administrador por CLI](0016-login-oidc-sesiones-csrf.md) | Aceptado |
| 0017 | [API REST de negocio: contratos con Pydantic, OpenAPI versionado y errores de autorización](0017-api-rest.md) | Aceptado |
| 0018 | [Limitador de peticiones de la API: cubos de fichas en memoria por usuario y por IP de login](0018-limitador-de-peticiones.md) | Aceptado |
| 0019 | [Minimización de datos: vaciar nombre y correo de usuarios sin acceso](0019-minimizacion-usuarios-sin-acceso.md) | Aceptado |
| 0020 | [Proxy de salida para el cliente OIDC](0020-proxy-de-salida-oidc.md) | Aceptado |
| 0021 | [Arquitectura del frontend: Vue 3, Feature-Sliced, cliente generado y sesión por cookie](0021-arquitectura-del-frontend.md) | Aceptado |
| 0022 | [Despliegue: Compose endurecido, Nginx, CI/CD y cadena de suministro](0022-despliegue-compose-nginx-ci.md) | Aceptado |
| 0023 | [Sistema visual: de «Brutalismo Técnico» a interfaz empresarial moderna](0023-sistema-visual-empresarial.md) | Aceptado |
| 0024 | [Ficheros que Semgrep omite por tamaño: visibles y salida incompleta](0024-ficheros-omitidos-por-tamano.md) | Aceptado |
| 0025 | [Contenedor sostenedor del workspace (volumen tmpfs compartido entre fetch y escaneo)](0025-contenedor-sostenedor-del-workspace.md) | Aceptado |
| 0026 | [Denegaciones de no miembros: aviso estructurado, no audit log](0026-denegaciones-de-no-miembros-en-log.md) | Aceptado |
| 0027 | [Sistema visual: consola de triage densa](0027-consola-de-triage.md) | Aceptado |
| 0028 | [Vistas de organización: estadísticas, actividad, reglas, riesgos, dependencias, política y auditoría](0028-vistas-de-organizacion.md) | Aceptado |
| 0029 | [Barra lateral con iconos funcionales y vistas de triage: PR, cola, fallidos, escáneres, sesiones y exportación](0029-navegacion-lateral-y-vistas-de-triage.md) | Aceptado |
| 0030 | [Administración de plataforma: usuarios, salud, webhooks y edición de la política](0030-administracion-de-plataforma.md) | Aceptado |
| 0031 | [El administrador de plataforma no cambia su propia pertenencia](0031-administrador-de-plataforma-sin-autoasignacion.md) | Aceptado |
| 0032 | [Política de quality gates por defecto para organizaciones nuevas](0032-politica-de-quality-gates-por-defecto.md) | Aceptado |
| 0033 | [Un usuario de Redis por servicio](0033-usuarios-de-redis-por-servicio.md) | Aceptado |

Este índice se mantiene al día. Los ADRs aceptados no se editan: se sustituyen por otros nuevos.
