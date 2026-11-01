# Fase 7 — Informe ejecutivo de la revisión de seguridad

Fecha: 2026-10-28. Detalle técnico, pruebas y evidencias en
[`fase7-revision.md`](fase7-revision.md); riesgos residuales en
[`../threat-model.md`](../threat-model.md) (RR-01 a RR-33).

## Conclusión

**NAVE no está lista para producción todavía, pero no por un problema de diseño.** La
arquitectura (escaneos en contenedores efímeros sin red, salida a Internet solo hacia
GitHub, autorización en cada caso de uso, auditoría inmutable, secretos solo en ficheros)
ha resistido la revisión. Lo que faltaba era que varias piezas funcionaran de verdad de
extremo a extremo: antes de esta fase **ningún escaneo podía terminar** y parte de la
cadena de suministro no existía. Eso está corregido y probado.

Quedan dos condiciones para el paso a producción (ver «Antes de producción»), ninguna
de las cuales exige cambiar el diseño.

## Qué se revisó

Revisión como atacante, por límite de confianza, de los diez componentes acordados:
sandbox y fetcher, `scan-runner`, proxy de salida, webhook de GitHub, autenticación y
sesiones, autorización y multi-tenancy, persistencia (PostgreSQL, Redis, Celery),
frontend, despliegue y cadena de suministro, y secretos y logs. `domain` y `application`
se revisaron por muestreo. Después, una revisión delta de las funciones añadidas con el
rediseño de la interfaz (ADR 0027 a 0030). Cada hallazgo se reprodujo contra la pila real en ejecución
siempre que fue posible, y cada corrección lleva un test de regresión.

## Resultados

33 hallazgos (F7-01 a F7-33):

| Severidad | Nº | Corregidos | Parciales | Aceptados con justificación |
|---|---|---|---|---|
| Bloqueante | 2 | 2 | — | — |
| Alto | 6 | 6 | — | — |
| Medio | 11 | 8 | 2 | 1 |
| Bajo | 14 | 4 | 1 | 9 |
| **Total** | **33** | **20** | **3** | **10** |

«Parciales»: F7-04 (falta el contrato de aislamiento de Trivy en CI), F7-13 (documentado;
pendiente de comprobar en producción) y F7-18 (Redis endurecido, pero un solo usuario para
todos los servicios).

### Los hallazgos que importan

| ID | Qué pasaba | Impacto |
|---|---|---|
| F7-05 (Bloqueante) | El código clonado nunca llegaba al contenedor de escaneo | Ningún escaneo podía completarse. Fallaba cerrado (sin falsos «aprobado») |
| F7-01 (Alto) | Un fichero de configuración vacío hacía abortar a Trivy | Sin análisis de dependencias, secretos ni IaC |
| F7-02 (Alto) | Semgrep omitía en silencio los ficheros de más de 2 MB | Quien controla un repositorio podía esconder código vulnerable al quality gate rellenando el fichero |
| F7-15 (Alto) | Con un escaneo activo en una rama, cualquier escaneo nuevo de esa rama fallaba | Commits sin escanear y errores 404 a usuarios autorizados |
| F7-24 (Alto) | La imagen de producción del proxy de salida no existía ni se publicaba | El control de salida a Internet no podía desplegarse |
| F7-32 (Bloqueante) | El worker pedía más salida de escáner de la que admite el runner | Ningún escaneo real podía completarse; lo destapó la prueba con GitHub real |
| F7-33 (Alto) | La base de datos de Trivy (1,5 GB) se copiaba a un tmpfs de 1 GiB | Trivy fallaba en todo escaneo real: sin análisis de dependencias ni secretos |
| F7-31 (Alto) | Un administrador de plataforma podía darse ADMIN en cualquier organización | El rol pensado solo para administrar la instalación daba acceso a todos los hallazgos y auditorías |
| F7-14 (Medio) | Dos administradores podían retirarse mutuamente a la vez | Organización sin administrador en 4 de 5 intentos medidos |
| F7-22, F7-25, F7-27 (Medio) | Lockfile del frontend sin hashes en 264 de 301 paquetes, CI que no construía nada con digests fijos, dependencias con CVE críticos | Cadena de suministro no verificable |
| F7-29 (Medio) | El log de acceso de la API aceptaba saltos de línea de la URL | Forja de líneas de log y secretos sin redactar |

**Patrón común:** casi todos los fallos graves estaban en piezas que parecían terminadas
porque sus tests unitarios pasaban, pero que nunca se habían ejecutado juntas contra los
binarios y servicios reales. La lección para el equipo es mantener las pruebas de extremo
a extremo y el pipeline de imágenes como parte obligatoria de CI.

## Riesgos que se aceptan

Ninguno es alto. Los más relevantes:

- **Exfiltración por `github.com`** (RR-25): el proxy filtra por dominio, no por uso.
  Quien comprometa la API o el worker podría subir datos a un repositorio propio. Mitigado
  por ser el único canal de salida y por tokens de solo lectura de un repositorio.
- **Un solo usuario de Redis** (RR-31): un componente comprometido controla las colas.
  Mejora propuesta: un usuario por servicio.
- **IP real del cliente** (RR-29, abierto): el límite de intentos de login exige que Nginx
  vea la IP real; hay que comprobarlo en el entorno de producción.
- **Administrador de plataforma** (RR-34): puede dar roles a otras cuentas en cualquier
  organización (no a sí mismo). Es la función pedida y queda auditada.
- **Tráfico interno sin TLS** (RR-32) y **paquetes base con CVE hasta refrescar
  digests** (RR-33): aceptables mientras todo corra en un único host.

## Antes de producción

1. **Despliegue en el host de producción con motor rootless y gVisor** (RR-20): en
   desarrollo (Docker Desktop) el aislamiento de escaneos no es un límite de seguridad.
2. **Comprobar la IP real del cliente** tras Nginx (RR-29) y programar la actualización de
   la base de datos de Trivy (RR-15).

## Estado de la verificación

Suites en verde al cerrar cada componente: backend 1959 tests tras la revisión delta
(unitarios, seguridad y e2e; más los de integración con PostgreSQL real), runner 278, despliegue 75 y frontend 238
antes del rediseño de la interfaz. Autoescaneo de la propia plataforma (Semgrep y Trivy)
con 0 hallazgos; 10 de 10 acciones de CI fijadas por SHA verificadas contra GitHub; las 7
imágenes propias construidas con digests fijos, sin vulnerabilidades corregibles salvo las
de RR-33. Revisión delta: 27 comprobaciones en vivo sobre las rutas nuevas, todas
conformes tras corregir F7-31.

**Escaneo real con GitHub (hecho el 2026-10-28):** GitHub App propia instalada en un
repositorio privado de prueba, webhooks por túnel. Tras corregir F7-32 y F7-33, una PR con
código vulnerable produjo un escaneo completo en 13 s (23 hallazgos de Semgrep y Trivy),
un check y un comentario en la PR, con salida a Internet solo hacia GitHub. Queda sin
verificar la subida de SARIF a Code Scanning (no disponible en repos privados gratuitos).

No verificado: lo enumerado en «Antes de producción».

**CI real en GitHub (hecho el 2026-10-31):** los seis jobs del workflow `ci` (backend con
PostgreSQL real, frontend, sandbox y runner, despliegue, autoescaneo e imágenes) pasan en
GitHub Actions en unos 4 minutos. Antes hubo que corregir los 50 errores de tipado de los
tests, un test de integración roto, la instalación del runner en el job del backend, un
`pip install` sin hashes que el propio `--require-hashes` rechazaba y la lista de acciones
permitidas. Actions solo admite acciones de GitHub y una lista cerrada de terceros, todas
fijadas por SHA. Sin verificar: el workflow `release` (firma con cosign, SBOM y publicación
en GHCR), que solo se dispara con etiquetas `v*` y no se ha lanzado.

**Mejoras posteriores sin coste (2026-10-31):**

- **Redis con un usuario por servicio** (ADR 0033, cierra RR-31): la API y el planificador
  solo pueden encolar; ya no pueden consumir, borrar ni ejecutar Lua. Permisos obtenidos
  observando el tráfico real y verificados con 0 denegaciones y pruebas negativas.
- **Política de quality gates por defecto** (ADR 0032): una organización nueva bloquea
  desde el primer escaneo las PR que introducen hallazgos CRITICAL o HIGH (antes salía
  NEUTRAL).
- **Contrato de aislamiento de escáneres en CI** (F7-04): las imágenes reales de Semgrep y
  Trivy se prueban contra un repositorio que intenta desactivarlas con su propia
  configuración; una mutación simulada lo hace fallar.
- `docker/setup-buildx-action` actualizado a Node 24.
- El contrato anterior destapó en su primera ejecución en CI otro fallo bloqueante (F7-34):
  las imágenes de escáner construidas desde el repositorio no podían ejecutar su envoltorio
  (`permission denied`), algo invisible en Windows. Corregido y verificado.

