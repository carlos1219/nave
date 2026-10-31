# Fase 7 — Revisión de seguridad final

Estado: en curso. Plan, alcance y método acordados el 2026-10-18: revisión como atacante por
límite de confianza (TB1–TB9), a fondo en los componentes 1–7 y por muestreo en `domain` y
`application`; el escaneo extremo a extremo con GitHub App real queda para el final.
Resumen para dirección en [`fase7-informe-ejecutivo.md`](fase7-informe-ejecutivo.md).

Severidad: **Bloqueante** (impide producción), **Alto**, **Medio**, **Bajo**, **Info**.
Estado: *Corregido* (con test de regresión), *Abierto*, *Aceptado* (con justificación).

## Registro de hallazgos

| ID | Componente | Sev. | Hallazgo | Estado |
|---|---|---|---|---|
| F7-01 | C1 Trivy | Alto | `trivy-secret.yaml` de la plataforma solo tenía un comentario: Trivy 0.75 aborta con `secrets config decode error: EOF`, así que **ningún escaneo de Trivy podía terminar** (fallo cerrado: sin SCA, secretos ni IaC) | Corregido |
| F7-02 | C1 Semgrep | Alto | Los ficheros mayores que `--max-target-bytes` (2 MB) se omiten **en silencio**: un `.py` de 2,4 MB con `eval(input())` da 0 resultados, sin notificación y con `executionSuccessful: true`. Quien controla el repo puede ocultar código al gate rellenando el fichero | Corregido (ADR 0024) |
| F7-03 | C1 fetcher | Bajo | El límite de número de ficheros (`max-files`) se aplica tras el `checkout`, no durante él | Aceptado |
| F7-04 | C1 contrato | Medio | `test_scanner_ignores_repo_config.py` solo cubre Semgrep y se omite si no hay Docker/imágenes; no hay prueba de aislamiento de configuración para Trivy, lo que dejó pasar F7-01 | Parcial: test estático añadido; falta contrato de Trivy en CI |
| F7-05 | C2 runner | **Bloqueante** | El workspace es un volumen `tmpfs` del driver `local`: se vacía al desmontarse, así que lo que clona el fetch **nunca llega** al contenedor de escaneo (Semgrep: «Invalid scanning root», `scanner_failed`). Ningún escaneo podía completarse; falla cerrado | Corregido (ADR 0025) |
| F7-06 | C2 CI | Medio | CI solo ejecutaba `ruff check src` del runner: sus 278 tests (`runner/tests`), que el threat model cita como verificación de AM-501/502/505, no corrían, ni `mypy` | Corregido |
| F7-07 | C2 runner | Bajo | No se distribuye perfil seccomp propio: `NAVE_RUNNER_SECCOMP_PROFILE` no se define en Compose y se aplica el perfil predeterminado del motor | Aceptado |
| F7-09 | C3 egress | Medio | Smokescreen restringe por dominio, no por puerto: `CONNECT github.com:22` (SSH) y `:80` se establecen (el resto de puertos agotan el plazo porque GitHub no escucha). `github.com` sigue siendo un canal de exfiltración para quien comprometa `api`, `worker` o un contenedor de fetch (p. ej. subir datos a un repositorio del atacante con credenciales propias) | Aceptado (RR-25) |
| F7-10 | C3 egress | Bajo | El proxy no define límites de peticiones ni de túneles concurrentes (`--max-concurrent-requests`, `--max-concurrent-connect-tunnels` sin usar); solo lo usan `api`, `worker` y fetch, y el contenedor tiene 128 MiB y 64 PID | Aceptado (RR-26) |
| F7-11 | C4 webhook | Bajo | Los eventos de instalación (`installation`, `installation_repositories`, `repository`) no pasan por la idempotencia por `X-GitHub-Delivery`: una entrega firmada capturada, reenviada, encola una sincronización cada vez | Aceptado (RR-27) |
| F7-14 | C6 authz | Medio | La regla «una organización no se queda sin administrador» no era segura bajo concurrencia: `count_admins` es un conteo sin bloqueo en READ COMMITTED, así que dos admins que se retiraban mutuamente a la vez comprobaban «quedan dos» y borraban ambos. Medido en vivo: **4 de 5 intentos** dejaron la organización sin administrador (solo recuperable por CLI) | Corregido |
| F7-15 | C6 / webhook | **Alto** | `ScanScheduler` marcaba el escaneo antiguo como sustituido por el nuevo **antes de insertar el nuevo**; `scans.superseded_by` es una FK inmediata y PostgreSQL la rechazaba (23503), que `translate_database_error` convertía en un 404. Efecto: mientras hubiera un escaneo activo en una rama, **cualquier escaneo nuevo de esa rama fallaba** (manual: 404 al usuario autorizado; webhook: entrega fallida y commit sin escanear). Los tests unitarios no lo veían porque el doble no tenía la FK | Corregido |
| F7-16 | C6 authz | Medio | Cada acceso de un **no miembro** escribía una fila `access.denied` en el audit log (inmutable, con cadena de hash): cualquier usuario autenticado podía generarlas sin límite y contra organizaciones que no existen (24 filas halladas), y ensuciaban el rastro de la organización de destino | Corregido (ADR 0026, RR-30) |
| F7-17 | C6 persistencia | Bajo | Toda violación de FK se traduce a «no encontrado» sin dejar rastro, lo que disfrazó F7-15 de problema de autorización | Corregido: queda un aviso con el nombre de la restricción |
| F7-18 | C7 Redis | Bajo | Un único usuario de Redis (`default`, `+@all -@admin`) para API, worker y planificador: quien comprometa la API puede leer, borrar o consumir cualquier cola, publicar en cualquier canal, ejecutar Lua con `EVAL` y **cargar funciones persistentes** (`FUNCTION LOAD` funcionaba). Además se descubrió que **no se puede denegar el scripting**: el `Lock` de redis-py (mutex de kombu para restaurar mensajes) libera con un script Lua, y con `-@scripting` el worker cae con `Unrecoverable error: NoPermissionError` | Corregido (2026-10-31): un usuario de Redis por servicio con permisos mínimos (ADR 0033, RR-31 cerrado) |
| F7-19 | C7 PostgreSQL | Bajo | `nave_app` y `nave_maintenance` pueden conectar a las bases `postgres` y `template1` (el `CONNECT` por defecto de PUBLIC no se revoca allí). No contienen datos y los roles no pueden crear objetos, pero no es mínimo privilegio | Aceptado (sugerencia en el informe) |
| F7-20 | C7 red interna | Bajo | El tráfico API/worker ↔ PostgreSQL y ↔ Redis no usa TLS (`ssl=off` en el servidor). Solo viaja por redes `internal: true` | Aceptado (RR-32) |
| F7-21 | C8 frontend | Bajo | `session.displayName` (nombre que da el IdP) se mostraba en la cabecera y en el inicio sin pasar por `visibleText`, a diferencia del resto de campos de origen externo. El backend ya sustituye los caracteres peligrosos al iniciar sesión, así que era defensa en profundidad | Corregido (test añadido) |
| F7-22 | C8 cadena de suministro | **Medio** | `frontend/package-lock.json` solo tenía `integrity` y `resolved` en **37 de 301 paquetes**: `npm ci` no podía verificar el tarball de los otros 264 (un registro o una red comprometidos habrían servido código distinto sin que nada fallara). Además `undici` (dependencia de desarrollo vía `jsdom`) tenía una vulnerabilidad `high` | Corregido: 301 de 301 con hash SHA-512 y origen `registry.npmjs.org`, `undici` 8.11.2 → 8.12.3, `npm audit` en 0; test que lo exige |
| F7-23 | C8 / Nginx | Bajo | El threat model (AM-701) daba por hecho `require-trusted-types-for 'script'` en la CSP, pero la cabecera real no lo llevaba | Corregido: activado y verificado en Chrome con todas las páginas |
| F7-24 | C9 egress-proxy | **Alto** | La cadena de producción del proxy no existía: `images.env.example` apuntaba a `ghcr.io/stripe/smokescreen:latest` (imagen que no existe), el workflow `release` no construía `egress-proxy` y la versión de Smokescreen no estaba fijada | Corregido: imagen propia compilada desde `v0.1.0` (`deploy/build-versions.env`), incluida en CI y en la matriz de `release`; test que exige las 7 imágenes propias |
| F7-25 | C9 CI | **Medio** | El job `images` de CI era un no-op: `deploy/images.lock.env` (digests de las imágenes base) no estaba en el repositorio, así que nada se construía con digests fijos | Corregido: lock generado por `pin_images.py` (10 bases), validado por test |
| F7-26 | C9 CI | **Medio** | El autoescaneo de CI habría fallado: Trivy marca como secreto crítico un token ficticio (`ghs_TTTT…`) del vector firmado `contracts/runner-protocol-vectors.json`, y `nginx/Dockerfile` sin `USER` (DS-0002) | Corregido: excepción mínima en `.github/trivy-secret.yaml` (solo esa ruta, con test) y `USER 101:101`; Semgrep y Trivy fs en 0 hallazgos |
| F7-27 | C9 cadena de suministro | **Medio** | Dependencias con CVE en imágenes propias: `certifi` (CRITICAL, imagen Semgrep) y `logrus`, `x/net`, `x/text` (HIGH, Smokescreen); sin Dependabot configurado | Corregido: `certifi` 2026.10.19 con hashes; el proxy se compila en un módulo propio con las tres dependencias elevadas (0 vulnerabilidades, probado: GitHub pasa, `example.com` se rechaza); `.github/dependabot.yml` (actions, npm, pip, docker) |
| F7-28 | C9 imágenes base | Bajo | Restan CVE HIGH en paquetes del sistema base: `libbrotli1` (Debian, imagen frontend) y `brotli-libs`/`libexpat` + binario `trivy` (Alpine, imagen Trivy) | Aceptado (RR-33): se resuelve al refrescar los digests con `pin_images.py`; `--ignore-unfixed` ya los filtra si no hay parche |
| F7-29 | C10 logs | **Medio** | El log de acceso de gunicorn (`--access-logformat` con `%(U)s`) escribía la ruta decodificada tal cual: `/api/v1/foo%0aINJECTED` generaba una línea de log falsa y un secreto en la ruta salía sin redactar (Nginx sí escapaba). Comprobado en vivo | Corregido: `AccessLogger` propio (`--logger-class`) que escapa control, comillas y barra invertida y redacta; test y comprobación en vivo tras reconstruir |
| F7-31 | C11 plataforma | **Alto** | Un administrador de plataforma podía darse a sí mismo ADMIN en cualquier organización (`PUT /platform/users/{propio}/memberships/{org}`) y leer hallazgos y audit log, aunque el ADR 0030 dice que el rol «no da acceso a datos de ninguna organización». Reproducido en vivo: 204 y luego 200 en el audit log de demo-corp | Corregido: 403 y `access.denied` (`reason=self`) auditado; tests y repetición en vivo. Residual RR-34 |
| F7-32 | C12 escaneo real | **Bloqueante** | El worker pedía por defecto hasta 256 MiB de salida de escáner (`NAVE_SCANNER_MAX_OUTPUT_BYTES`) y el runner solo admite 64 MiB (`NAVE_RUNNER_MAX_OUTPUT_BYTES`): **toda petición de escaneo real se rechazaba con 400** y el runner ni siquiera lo registraba. Los tests usaban límites propios y no lo veían | Corregido: valor por defecto del worker en 64 MiB, test cruzado de valores por defecto, el runner registra cada rechazo (solo la regla incumplida) |
| F7-33 | C12 escaneo real | **Alto** | El envoltorio de Trivy copiaba la base de datos de vulnerabilidades (1,5 GB) al tmpfs de 1 GiB del escaneo: `No space left on device` en todo escaneo de Trivy. Además el script `update_trivy_db.py` descargaba en un `/tmp` de 256 MB y fallaba igual | Corregido: la base de datos se enlaza en solo lectura (sin copia, ahorra 1,5 GB de RAM por escaneo) y la descarga usa el propio volumen como temporal; tests |
| F7-30 | C10 logs | Bajo | Huecos de la redacción: un JWT suelto y el valor de una cookie de sesión no se redactaban; el mensaje se recortaba a 2000 caracteres *antes* de redactar (el principio de un secreto cortado pasaba); un token `ghs_` pegado a una palabra no coincidía por el `\b` | Corregido: dos patrones nuevos, se redacta antes del recorte final y se quita el `\b`; tests de regresión |
| F7-12 | C5 auth | Bajo | Cada `GET /auth/login` anónimo escribe un flujo en la base de datos (caduca a los 10 min) con tope de 10 000 vivos; el limitador es por proceso de gunicorn (efectivo ×4: 40 inicios en ráfaga por IP, medido). Con unas 13 IP sostenidas se llena la tabla y el login responde 503 a todos hasta que caducan; las sesiones ya abiertas no se ven afectadas | Aceptado (RR-28) |
| F7-13 | C5 auth / despliegue | Medio | El login se limita por IP, pero con Docker Desktop (y, según el controlador de puertos, con motor rootless o un balanceador sin `real_ip`) todos los clientes llegan con la misma IP: comparten un único cubo de login. Verificado en dev: toda la actividad aparece como `172.19.0.1`. No estaba documentado como requisito de despliegue | Corregido en documentación (`despliegue.md` §6 punto 9); pendiente de verificar en producción |
| F7-08 | C2 runner | Bajo | Servidor HTTP del runner con un hilo por conexión (tope 64, plazo de socket 10 s): un cliente lento podría ocupar las 64 conexiones. Solo es alcanzable desde la red `runner` (el worker) | Aceptado |

## C1 — Sandbox y fetcher (TB5, TB6)

Revisado: `deploy/scanner-sandbox/fetcher/{nave_fetch.py,pathguard.py,Dockerfile}`, envoltorios
`nave-scan` de Semgrep y Trivy, sus Dockerfiles y configuración, y el parser SARIF respecto a
`suppressions`.

### Vectores revisados

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Argumentos del fetcher (inyección de flags) | `parse_arguments`: claves cerradas, patrón por clave, sin repetidas; token por stdin con patrón | Correcto por lectura |
| Hooks, submódulos, símbolos, protocolos git | `HARDENING` (`core.hooksPath=/dev/null`, `core.symlinks=false`, `protocol.allow=never`, `transfer.fsckObjects`, `credential.helper=`, …), `GIT_CONFIG_NOSYSTEM/GLOBAL`, `python -E -s` | Correcto por lectura; el clonado real contra GitHub no se ha ejecutado (pendiente de GitHub App) |
| Fuga del token | variable de entorno del proceso git, cabecera ligada a `https://github.com/`, `http.followRedirects=false`, salida de git capturada y nunca impresa | Correcto por lectura |
| Escape por rutas y enlaces | `sanitize_tree` + `resolve_inside` | **Verificado en la imagen real** (usuario 65532, rootfs de solo lectura, sin red): enlaces absoluto, relativo con `..`, a directorio, colgante y en bucle y una FIFO se eliminan (5 enlaces, 1 especial); se conservan ficheros normales y nombres con espacios; `../x`, `/etc/passwd`, `a/../../b` y un byte nulo se rechazan |
| Zip bomb / árbol enorme | `max-files`, `max-bytes` | **Verificado**: ambos límites lanzan `TreeTooLargeError`. Ver F7-03 |
| Configuración del repo secuestra Semgrep | `--config /rules`, `--x-ignore-semgrepignore-files`, `--no-git-ignore`, `--disable-nosem`, cwd fuera del repo | **Verificado con el binario real y sandbox** (`--network none`, `--read-only`, `--cap-drop ALL`): un `.semgrepignore` con `*`, un `.gitignore` que lista el fichero, `.semgrep.yml` y `.semgrep/` hostiles no ocultan ni añaden resultados |
| `# nosemgrep` | Semgrep lo marca como `suppressions: inSource` aunque se pase `--disable-nosem`; el parser de NAVE lo ignora | **Verificado**: el resultado sigue en el SARIF con la marca y `test_scanner_side_suppressions_are_not_honoured` cubre que NAVE no la respeta |
| Configuración del repo secuestra Trivy | `--config`, `--ignorefile`, `--secret-config` explícitos, cwd fuera del repo | **Verificado** con Trivy 0.75.0 y sandbox, tras corregir F7-01: `trivy.yaml` con `skip-dirs`, `trivy-secret.yaml` con `allow-rules` global, `.trivyignore` y `.trivyignore.yaml` hostiles no suprimen el secreto ni la misconfiguración |
| Evasión por tamaño | `--max-target-bytes 2000000` | **Falla**: ver F7-02 |
| Salida del escáner como entrada hostil | límites del parser SARIF (AM-601/602) | No revisado aquí; se cubre en C2/C7 y muestreo de `infrastructure` |

### F7-01 — Trivy no podía escanear

- **Evidencia:** `docker run ... nave-trivy:dev` con los flags de `nave-scan` → `FATAL ... secrets config decode error: EOF`. Reproducido con la configuración de la propia imagen, sin ninguna entrada del repo.
- **Causa:** `deploy/scanner-sandbox/trivy/trivy-secret.yaml` era solo un comentario (documento YAML vacío).
- **Corrección:** `disable-rules: []` (ninguna regla desactivada). Verificado en la imagen reconstruida.
- **Regresión:** `deploy/tests/test_artefactos.py::test_configuracion_de_trivy_de_la_plataforma_es_yaml_cargable` (48 tests de despliegue en verde).
- **Impacto:** el sistema fallaba cerrado; no hubo exposición, pero SCA, secretos e IaC no funcionaban.
- **Sin verificar:** el escaneo `vuln` de Trivy, porque requiere el mirror `/trivy-db` (`update_trivy_db.py`), que no está cargado en esta máquina. Lo cubre la verificación final.

### F7-02 — Ficheros grandes omitidos en silencio

- **Evidencia:** repo con `big.py` (2,4 MB, `eval(input())` al final) → `results: 0`, `toolExecutionNotifications: []`, `executionSuccessful: true`.
- **Riesgo:** el quality gate puede pasar sobre un repo que contiene código vulnerable oculto; el atacante necesita solo poder hacer push (AM-502/503, actor con acceso al repo).
- **Decisión (responsable del proyecto):** reportar los omitidos, sin cambiar el límite. Detalle y alternativas descartadas en `docs/adr/0024-ficheros-omitidos-por-tamano.md`.
- **Corrección:** `nave-scan` ejecuta Semgrep a un fichero temporal y `nave_oversized.py` añade un resultado `nave.scan.oversized-file-skipped` (nivel `warning`, → `MEDIUM`) por cada fichero omitido, hasta 100, más un resumen sin ubicación si hay más. El adaptador `RunnerScanner` (`incomplete_when`) marca la salida como incompleta: línea `WARNING` en el registro del escaneo con `truncated=true`, veredicto `NEUTRAL` en lugar de `PASS`, sin cierre automático de findings. El canal es el SARIF porque del contenedor solo llega la salida estándar.
- **Verificación:** con la imagen real de Semgrep y el sandbox, dos ficheros grandes (uno con espacios y `ñ`) aparecen como resultados junto al hallazgo normal. Tests: `deploy/tests/test_semgrep_oversized.py` (18) y 3 nuevos en `backend/tests/unit/infrastructure/test_scanner_adapters.py`. Suite de despliegue 66 en verde; backend 1846 en verde, ruff, mypy e import-linter limpios.
- **Residual:** umbral fijo de 2 MB; la política «fallar si hay ficheros grandes» no es configurable todavía.

### F7-03 — `max-files` posterior al checkout

El fetcher cuenta ficheros después de `git checkout`; un árbol con millones de ficheros vacíos consume inodos y memoria de la tmpfs antes de ser rechazado. Mitigado por `size=` del tmpfs, límite de memoria y PIDs del contenedor, el timeout del runner y la comprobación previa de tamaño por la API de GitHub. Aceptado como riesgo bajo.

### F7-04 — Cobertura de contrato de escáneres

El contrato de «el escáner ignora la configuración del repo» solo existe para Semgrep y no se ejecuta sin Docker. Se añadió un test estático de la configuración de Trivy. Falta un test de contrato equivalente para Trivy (secretos y misconfiguración) en CI, con las imágenes construidas.

### Nota de superficie de ataque

- Entradas no confiables: contenido del repo (nombres, enlaces, tamaños, configuración propia), salida de git (stderr), SARIF/JSON del escáner.
- Mitigaciones verificadas hoy: saneado del árbol y límites, aislamiento de la configuración de Semgrep y de Trivy, `nosemgrep` no honrado, sandbox sin red y rootfs de solo lectura, ficheros omitidos por tamaño visibles (F7-02, ADR 0024).

## C2 — `scan-runner` (TB4, TB5)

Revisado: `runner/src/nave_runner/{protocol,server,schema,service,templates,engine,config,sweeper,__main__}.py`, `runner/Dockerfile`, el servicio `scan-runner` de `docker-compose.yml` y los tests de `runner/tests`.

### Vectores revisados

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Falsificación de peticiones | HMAC-SHA256 sobre método, ruta, marca de tiempo, nonce y hash del cuerpo; `compare_digest`; ventana de ±60 s; nonce registrado solo con firma válida; se rechaza lo firmado antes del arranque; respuesta única sin motivo | **Verificado en vivo** (peticiones firmadas desde el worker con la clave real): sin firma, firma de otra clave, marca de hace 120 s y de +120 s, firma sobre otra ruta y cuerpo alterado → 401; **replay del mismo nonce → 401** |
| Esquema abierto (inyección de imagen, flags, montajes, red) | Esquema cerrado: claves exactas, ids por expresión regular, enteros acotados por el máximo del runner, escáner solo de la lista | **Verificado en vivo**: clave extra o ausente, JSON con clave duplicada, `NaN`, `run_id` con `../` o mayúsculas, escáner no habilitado o con flags (`semgrep --config=x`), plazo o salida fuera de rango (negativo, booleano, excesivo), paso desconocido, `owner`/`name`/token malformados → todos 400 |
| Transporte | Cuerpo ≤ 4096 B, sin `chunked`, `Content-Length` obligatorio, sin keep-alive, ruta única | **Verificado en vivo**: cuerpo grande → 413, ruta distinta o `GET /v1/runs` → 404, `Transfer-Encoding: chunked` → 400; `/healthz` responde sin firma solo `status` |
| Construcción de contenedores | Plantillas fijas (`templates.py`); ningún valor de la petición llega a flags, montajes o imagen | **Verificado en vivo** sobre el contenedor de escaneo real (`docker inspect`): usuario 65532, red `none`, rootfs de solo lectura, `cap_drop ALL`, `no-new-privileges`, 256 PID, 2 GiB (sin swap extra), 2 CPU, workspace montado **de solo lectura**, `/tmp` tmpfs `noexec,nosuid,nodev`, sin secretos en el entorno, sin driver de log, imagen por digest, etiquetas de barrido |
| Límites de tiempo y salida (AM-505) | `timeout` con `kill` y `rm --force`; salida con tope que mata el contenedor | **Verificado en vivo**: plazo de 1 s → 504, tope de 100 B → 413, sin contenedores residuales y un escaneo normal inmediato funciona |
| Limpieza | `release`, descarte tras fallo y barrido de huérfanos por etiqueta y nombre validado | **Verificado en vivo**: tras `release` no queda contenedor ni volumen de la ejecución; el barrido reconoce `nave-hold-*` (test) |
| Acceso al motor | El runner es el único con el socket (ADR 0002); imagen sin dependencias, usuario 65532, red `runner` interna | Correcto por lectura; en producción exige motor rootless y gVisor (RR-20) |
| Handoff del workspace entre fetch y escaneo | Volumen tmpfs compartido | **Falla**: F7-05, corregido con ADR 0025 |

### F7-05 — El escaneo nunca recibía el código

- **Evidencia:** (1) escaneo con la plantilla real sobre un workspace poblado → `Invalid scanning root: repo`, código 2, `scanner_failed`; (2) prueba aislada con Docker 29.8.2: un contenedor escribe en un volumen tmpfs, otro lo monta después y está vacío; con un contenedor sostenedor activo el contenido persiste.
- **Causa:** el driver `local` con `type=tmpfs` monta al primer uso y desmonta al terminar el último contenedor; el fetch termina antes de que empiece el escaneo.
- **Corrección (ADR 0025):** contenedor sostenedor sin red por workspace (`nave-hold-<id>`), retirado el último en `release`/descarte/barrido; el escaneo solo se acepta si el sostenedor sigue vivo (si no, `unknown_workspace`, nunca un árbol vacío).
- **Verificación:** nuevos tests de plantilla, servicio y motor (runner: 278 en verde, ruff, formato y mypy limpios; cobertura 97 %); flujo real sobre el stack: 200 con los resultados del repo hostil (incluidos `.semgrepignore` ignorado y fichero grande reportado) y limpieza completa.
- **Impacto:** funcional (falla cerrado). Por qué no se vio antes: el motor era un doble en los tests y la primera ejecución real nunca llegó a escanear (sin GitHub App).
- **Sin verificar:** el paso de fetch real contra GitHub a través del proxy y la repetición en el motor rootless de producción.

### F7-06 — Tests del runner fuera de CI

El paso «Runner» de CI hacía solo `ruff check src`. Ahora ejecuta `ruff check src tests`, `mypy` y `pytest --cov` (umbral 90 %; hoy 97 %). Los tests de despliegue (66) siguen en verde con el cambio de workflow.

### F7-07 y F7-08

- **F7-07:** el runner admite `NAVE_RUNNER_SECCOMP_PROFILE` pero no se define ni se distribuye ningún perfil; los contenedores usan el perfil predeterminado del motor (más gVisor en producción). Cumple el criterio de «perfil seccomp» de §8 con el perfil estándar; un perfil propio por escáner requeriría pruebas de syscalls dedicadas.
- **F7-08:** el servidor HTTP del runner usa un hilo por conexión con tope de 64 y plazo de socket; un cliente que gotee bytes puede ocuparlas todas. Solo lo alcanza el worker (red `runner` interna, firma obligatoria antes de cualquier trabajo), así que el impacto es un fallo del propio worker o de quien lo comprometa (que ya tiene la clave).

### Nota de superficie de ataque

- Entradas no confiables para el runner: ninguna directa; todo llega firmado del worker y se valida con esquema cerrado. Nada de la petición elige imagen, flags, montajes ni red.
- Riesgo principal sigue siendo el socket del motor (RR-20): el runner lo necesita; su exposición se limita por la red interna, la firma HMAC, el esquema cerrado y las plantillas fijas.
- Sostenedor (ADR 0025): contenedor mínimo sin red ni secretos; solo monta el volumen de su ejecución.

## C3 — Egress (TB8, parte de TB1)

Revisado: `deploy/egress-proxy/{acl.yaml,config.yaml,Dockerfile,healthcheck.go}`, redes y servicios de `docker-compose.yml`, y el test estático de topología (`deploy/tests/test_compose.py`).

### Vectores revisados

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Salida directa a Internet desde un contenedor comprometido | Redes `edge`, `internal`, `runner` y `egress` con `internal: true`; solo `egress-proxy` (y `frontend`) están en `public` | **Verificado en vivo** con `docker network ls` y desde `api`, `worker` y `scan-runner`: conexión TCP directa a 1.1.1.1:443, 8.8.8.8:53, 140.82.112.3:443 y 142.250.200.14:80 → bloqueada (`ENETUNREACH`) |
| Exfiltración por DNS | Las redes internas no reenvían DNS externo | **Verificado**: `github.com` y `example.com` no resuelven desde `api`, `worker` y `runner`; solo se resuelven nombres internos |
| Segmentación entre servicios | Cada servicio solo en las redes necesarias | **Verificado**: `api` no resuelve `scan-runner`; `runner` no resuelve `postgres`, `redis`, `api` ni `egress-proxy`; `scheduler`, `redis` y `postgres` solo en `internal`. Cubierto además por `test_redes` |
| Allowlist de dominios | Smokescreen con `default` y tres dominios | **Verificado** (también en `despliegue.md` §7): solo `github.com`, `api.github.com`, `codeload.github.com`. Variantes de nombre: `github.com.evil.com`, `evilgithub.com`, `www.github.com`, `objects.githubusercontent.com` → 407; `evil.com#github.com` y `github.com@evil.com` → 400; mayúsculas y punto final equivalen a `github.com` (mismo host) |
| SSRF hacia red interna y metadatos | Smokescreen bloquea rangos privados, loopback y enlace local | **Verificado**: `postgres:5432`, `redis:6379`, `scan-runner:8080`, `172.19.0.2:5432`, `127.0.0.1`, `[::1]`, `169.254.169.254` y su variante HTTP plano → 407 |
| Proxy HTTP plano | Solo dominios permitidos | **Verificado**: `http://github.com/` → 301; cualquier otro destino → 407 |
| Camino real del fetch | `git` con `HTTPS_PROXY` | **Verificado**: contenedor de la imagen del fetcher en `nave-egress`: `git ls-remote` de un repo público por el proxy → OK; sin proxy no resuelve el DNS; gitlab.com → `CONNECT tunnel failed, response 407`; `nave-fetch` real con token inválido termina con 11 (fallo permanente), no con 12 |
| Restricción por puerto | — | **No existe**: ver F7-09 |
| Puertos publicados | Solo `frontend` | **Verificado**: 80 y 443 en el frontend; Keycloak solo en `127.0.0.1:8444` (perfil dev); sin publicar `postgres`, `redis`, `api`, runner ni proxy |

### F7-09 — Dominio permitido en cualquier puerto

Smokescreen filtra por dominio y por rangos de IP, pero su ACL no admite puertos. Con `github.com` permitido, un cliente de la red `egress` puede abrir túneles a `github.com:22` y `:80` además de `:443`. Los demás puertos fallan solo porque GitHub no escucha en ellos. El riesgo real no es el puerto sino que **`github.com` es un destino de subida**: un `api`, `worker` o fetch comprometido podría enviar datos a un repositorio del atacante con credenciales propias. Es inherente a permitir GitHub y no se puede cerrar con la allowlist actual. Mitigaciones existentes: solo `api`, `worker` y los contenedores de fetch están en `egress`; el escáner no tiene red; el token de clonado es de un repositorio y solo lectura (ADR 0013/0014); no hay DNS externo. Aceptado.

### F7-10 — Sin límites en el proxy

Smokescreen admite `--max-concurrent-requests`, `--max-request-rate` y `--max-concurrent-connect-tunnels`; no se usan. Los clientes son de confianza y el contenedor está acotado (128 MiB, 64 PID, 0,5 CPU); un cliente comprometido podría degradar el proxy y con ello los escaneos. Fijar topes demasiado bajos causaría falsos fallos, por lo que queda como mejora tras medir el uso real.

### Observaciones sin riesgo

- Una petición al propio proxy (`GET /metrics`) devuelve 500 genérico; no revela nada.
- `frontend` y, en dev, `keycloak` están en `public`; el primero lo necesita para publicar puertos.
- En producción el proveedor OIDC debe añadirse a la ACL (`despliegue.md` §5): en dev el login va directo (`NAVE_OIDC_PROXY_URL` vacío) y esa ruta por el proxy no se ha probado.

### Nota de superficie de ataque

- Entradas no confiables: destino de las conexiones de clientes ya comprometidos, nombre de host y puerto del `CONNECT`.
- Mitigaciones verificadas: sin ruta ni DNS fuera del proxy, allowlist estricta por dominio, bloqueo de rangos internos y de metadatos.
- Residual aceptado: GitHub como canal de salida (RR-25) y proxy sin límites de uso (RR-26).

## C4 — Webhook de GitHub (TB2)

Revisado: `delivery/http/webhook.py`, `webhook_models.py`, `strict_json.py`, `security.py` (`hmac_signed`), `infrastructure/vcs/webhook_verifier.py`, `application/use_cases/handle_github_event.py`, límites de Nginx para `/api/v1/webhooks/` y los tests de `backend/tests/unit/delivery/test_webhook.py`.

### Vectores revisados

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Falsificación (AM-201) | HMAC-SHA256 sobre el cuerpo crudo antes de parsear, `compare_digest`, un o dos secretos (rotación) comprobados siempre ambos | **Verificado en vivo** con firmas reales a través de Nginx: sin cabecera, vacía, `sha1=`, hex en mayúsculas, 63 y 65 caracteres, firma de otro cuerpo, secreto incorrecto y cuerpo alterado tras firmar → 401 con la misma forma de respuesta. Una firma con espacios alrededor se acepta porque HTTP recorta los espacios del valor de la cabecera: no es una evasión |
| Cuerpo crudo intacto por el proxy | Nginx no reescribe el cuerpo | **Verificado**: la firma sobre 4 MiB llega válida a Flask, también con `Transfer-Encoding: chunked` (Nginx lo ensambla) |
| Orden de comprobaciones | tamaño → firma → tipo de contenido → cabeceras → JSON estricto → esquema → caso de uso | **Verificado**: sin firma válida nada se parsea ni toca la base de datos (un evento desconocido con JSON roto y firma válida → 202; con firma mala → 401) |
| Tamaño y recursos (AM-203) | 5 MiB en Nginx y Flask, 10 r/s por IP con ráfaga de 20 | **Verificado**: 5,5 MiB y 20 MiB → 413 en Nginx; 4 MiB firmado procesado en 0,26 s; ráfaga de 80 peticiones → 28 aceptadas y 52 con 429, y vuelve a responder 202 tras 3 s |
| Replay (AM-202) | Idempotencia por `X-GitHub-Delivery` (restricción única) | **Verificado**: la misma entrega repetida, también con cuerpo distinto firmado, no crea un segundo escaneo (+1 escaneo y 6 entregas registradas; las repeticiones no suman). Excepción: eventos de instalación (F7-11) |
| JSON hostil | `loads_strict`: UTF-8, claves duplicadas, `NaN`, profundidad 32; esquemas estrictos solo con ids, SHA, ref y número de PR | **Verificado**: JSON mal formado, clave duplicada, `NaN`, profundidad 5000, UTF-8 inválido, `after` que no es SHA, ids como cadena, negativos y > 2^63-1, `ref` de 300 caracteres y con salto de línea → 400. Campos con URLs hostiles (`clone_url`, `full_name`) se ignoran |
| Cabeceras y método | `X-GitHub-Delivery` UUID canónico en minúsculas; contenido `application/json`; solo POST | **Verificado**: `Delivery` no UUID o en mayúsculas → 400; `text/plain` o sin tipo → 415; GET y PUT → 405; ruta con barra final → 404 |
| Autorización por instalación (AM-204) | Repo por id de GitHub, coincidencia con la instalación dueña, allowlist | **Verificado**: repo desconocido, borrado de rama (SHA a ceros) y tags → 202 sin acción (`IGNORED`); instalación ajena → 202 `REJECTED` con entrada de auditoría `webhook.rejected` / `installation_mismatch` |
| Pull requests de forks (AM-205) | Política `off` por defecto; `repo` nulo cuenta como fork | **Verificado**: PR de la propia repo → `ENQUEUED` con ref `refs/pull/41/head`; PR desde fork y desde fork borrado, acciones `labeled` y `closed` → `IGNORED` |
| URLs del payload (AM-206) | Nunca se usan; el repo se resuelve por id y la URL se construye con `owner/name` validados | Correcto por lectura y test; confirmado que las URLs hostiles del payload no tienen efecto |
| Eventos de instalación | Solo se encola el id de la instalación | **Verificado**: sin `installation.id` → 400, con él → 202. Ver F7-11 |
| Fuga de secretos en logs | Los logs no incluyen secretos ni firmas | **Verificado**: ni el secreto del webhook ni ninguna cabecera `sha256=` aparecen en los logs de `api`, `worker` ni `frontend` tras las pruebas |
| Camino completo | Webhook → escaneo `QUEUED` → worker | **Verificado**: el escaneo creado por un push firmado llega al worker (queda `FAILED` en dev por la clave marcador de la GitHub App: `github status 404`, sin datos sensibles en el error) |

### F7-11 — Eventos de instalación sin idempotencia

`_sync_installation` encola la sincronización sin comprobar `X-GitHub-Delivery`. Para repetirla hace falta una entrega firmada válida (solo GitHub la produce), la acota Nginx (10 r/s por IP) y cada sincronización solo consulta a GitHub por esa instalación. Mejora posible: agrupar sincronizaciones por instalación (candado en Redis con plazo) o registrar la entrega como en los eventos de escaneo. Aceptado.

### Observaciones sin riesgo

- Los 429 de Nginx son HTML y no `problem+json`; GitHub los trata como fallo y reintenta.
- Celery registra un aviso engañoso («superuser privileges») en el arranque del worker aunque corre como 65532: la imagen no tiene entrada en `/etc/passwd` para ese uid y Celery supone root. No afecta al aislamiento (el proceso es no root, `Uid: 65532`); se puede silenciar añadiendo el usuario a la imagen.
- Con las entregas de GitHub reales, el límite por IP (10 r/s) se aplica a las IP de GitHub, que comparten todos sus clientes; es holgado para una organización, pero conviene vigilarlo si crece el número de repositorios.

### Nota de superficie de ataque

- Entradas no confiables: cuerpo, cabeceras y firma de cualquier cliente de Internet; tras la firma válida, el contenido del payload sigue tratándose como dato (solo ids, SHA, ref y número de PR).
- Mitigaciones verificadas: firma sobre el cuerpo crudo antes de cualquier trabajo, JSON y esquemas estrictos, idempotencia, límites de tamaño y de ritmo, política de forks desactivada por defecto y auditoría de instalaciones ajenas.
- Residual: replays de eventos de instalación (RR-27).

## C5 — Autenticación y sesiones (TB1)

Revisado: `delivery/http/{auth,security,rate_limit,app}.py`, `application/use_cases/login.py`, `application/services/session_tokens.py`, `infrastructure/auth/oidc.py`, `bootstrap/api_config.py`, `deploy/nginx/snippets/proxy-api.conf` y el esquema de `sessions`, `login_flows` y `audit_log`. Todo lo siguiente se probó con **un login OIDC real** contra el Keycloak de desarrollo (credenciales de prueba `dev`/`dev` del proyecto), a través de Nginx.

### Vectores revisados

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Inicio de login | Code + PKCE S256, `state` y `nonce` aleatorios de ≥ 43 caracteres, `redirect_uri` fija | **Verificado**: 302 al emisor correcto; `next` y `redirect_uri` del cliente se ignoran; `state` y `nonce` distintos en cada intento; solo GET (POST → 405) |
| Cookie de enlace del flujo | `__Host-nave_login`, vida de 10 min | **Verificado**: `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, sin `Domain`, caducidad ≤ 10 min; se borra al completar el login |
| Cookie de sesión | `__Host-nave_session`, valor opaco | **Verificado**: `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, sin `Domain`, `Max-Age` ≤ 12 h; el valor no aparece en ninguna URL |
| Almacén de sesiones | Solo el hash SHA-256 del token | **Verificado en la base de datos**: `token_hash` de 32 bytes, sin valor de cookie; `nave_app` solo puede `INSERT`/`SELECT` en `audit_log` (añadir solo) |
| Login CSRF / robo del `state` | `state` ligado al navegador con cookie de enlace; se consume antes de comparar | **Verificado**: callback de un flujo con la cookie de enlace de otro navegador → 401, y después con la correcta → 401 (el `state` ya está quemado); reutilizar un callback ya usado → 401 sin segunda sesión |
| Fijación de sesión | Token nuevo en cada login | **Verificado**: con una cookie de sesión fijada por el atacante antes del callback, el servidor emite otro token; el valor fijado no autentica (401) y el nuevo sí |
| Callback hostil | Validación de forma antes de tocar la base de datos | **Verificado**: sin parámetros, `state` inventado o con caracteres SQL, `state` repetido, `code` falso aceptado por forma pero rechazado por el IdP, error del IdP (`access_denied`) → todos 401. Un `code` de 5000 caracteres recibe 400 de gunicorn (límite de línea de petición) |
| Token de sesión inválido | Formato `[A-Za-z0-9_-]{43,128}` comprobado antes de la base de datos | **Verificado**: formato inválido, 43 caracteres inexistentes, inyección SQL, 5000 caracteres y nombre sin prefijo `__Host-` → 401 |
| CSRF en métodos no seguros | `Origin` exacto + token por sesión | **Verificado** (PUT de rol): sin `Origin` ni token, sin token, `Origin` ajeno, `null`, con barra final o `http`, token ajeno o de 5000 caracteres → 403; con ambos correctos se supera la comprobación; sin cookie → 401. GET no exige CSRF |
| CORS | Misma origen, sin CORS | **Verificado**: `GET /session` con `Origin` ajeno y preflight `OPTIONS` no devuelven cabeceras `Access-Control-*` |
| Métodos | Cierre de sesión solo por POST con CSRF | **Verificado**: `GET /auth/logout` → 405 y la sesión sigue viva; POST sin CSRF → 403 sin cerrar |
| Logout | Revocación en el servidor | **Verificado**: con `Origin` y token → 204 y cookie borrada; reutilizar la cookie antigua → 401; segundo logout → 401. Auditoría `session.created` / `session.ended` |
| Rutas sin sesión | Denegar por defecto | **Verificado**: organizaciones, findings, escaneos, finding y sesión → 401 sin sesión |
| Límite de login y `X-Forwarded-For` | Cubo por IP (20/min, ráfaga 10), Nginx sobrescribe `X-Forwarded-For` con `$remote_addr`, la API confía en un salto | **Verificado**: 60 inicios con un `X-Forwarded-For` distinto en cada uno → 40 aceptados y 20 con 429 y `Retry-After`: falsificar la cabecera no da cubos nuevos |
| ID token | Firma asimétrica (`RS256`, `PS256`, `ES256`, `EdDSA`), `iss`, `aud`, `exp`, `iat`, `nonce`, `azp`; identidad por `(iss, sub)` y correo solo si está verificado; endpoints del mismo origen que el emisor | Correcto por lectura y por tests unitarios; el login real contra Keycloak confirma el camino feliz. Sin probar en vivo: JWT alterado, `alg: none`, `nonce` ajeno, que cubren los tests de `oidc.py` |

### F7-12 — Llenado de la tabla de flujos de login

`BeginLogin` rechaza con 503 cuando hay 10 000 flujos vivos. Cada inicio anónimo crea uno y el limitador es por proceso (cuatro procesos: ráfaga efectiva 40 por IP, medida), así que con unas 13 IP sostenidas se llena la tabla en 10 minutos y nadie nuevo puede iniciar sesión hasta que caducan. No hay robo ni elevación: solo indisponibilidad temporal del login. Las sesiones abiertas siguen funcionando. Aceptado (RR-28); mejora posible: límite de flujos vivos por IP o limitador compartido en Redis.

### F7-13 — IP de cliente

El límite de login es por IP y su exactitud depende de que Nginx vea la IP real del cliente. En desarrollo (Docker Desktop) todos los clientes aparecen como `172.19.0.1`; en producción puede ocurrir lo mismo con un motor rootless según el controlador de puertos, o con un balanceador delante. Efecto: un único cubo para todos y 429 en el login cuando varios entran a la vez (denegación a usuarios legítimos, no un fallo de seguridad). Se ha añadido el punto 9 a `despliegue.md` §6 con la comprobación y las soluciones.

### Observaciones sin riesgo

- `sessions.csrf_token` y, en `login_flows`, `nonce` y `code_verifier` se guardan en claro: no sirven por sí solos (el token de sesión se guarda como hash; el `code_verifier` necesita además el `code`, y dura 10 minutos).
- Los intentos fallidos de autenticación (401, 429) quedan en el registro de acceso pero no en la auditoría; solo se auditan `session.created` y `session.ended`. Conviene alertar por tasa de 401 y 429 en el login.
- `GET /api/v1/organizations/{id}` responde 404 porque la ruta no existe (la página usa `/repositories` y `/members`); no es una fuga.
- Los límites por usuario son por proceso (×4), como documenta el ADR 0018.
- La sesión caduca a las 12 h en términos absolutos y a los 30 min de inactividad (valores por defecto); la inactividad se comprueba en el servidor y no se probó esperando, está cubierta por tests unitarios.

### Nota de superficie de ataque

- Entradas no confiables: cookies, `state`, `code`, cabeceras `Origin` y `X-CSRF-Token`, `X-Forwarded-For`, y el contenido del ID token (tras verificar la firma del emisor).
- Mitigaciones verificadas: flujo de código con PKCE y enlace al navegador, token de sesión opaco con solo el hash en la base de datos, `__Host-` con `Secure`/`HttpOnly`/`SameSite`, CSRF doble, sin CORS, denegación por defecto, revocación en el logout y limitador por IP no manipulable desde la cabecera.
- Residual: indisponibilidad del login por llenado de flujos (RR-28) y dependencia de la IP real para el límite (RR-29).

## C8 — Frontend (TB7: datos → navegador)

Revisado: `frontend/src` completo (router, cliente HTTP, almacén de sesión, `visibleText`, todos los `.vue`), `package.json`, `package-lock.json`, `.npmrc`, `deploy/nginx/{Dockerfile,nginx.conf,snippets}` y los 20 ficheros de tests. Verificado con la batería completa en un contenedor Node (instalación desde el lockfile con scripts desactivados) y en **Chrome real** contra el stack en marcha, con un finding y un log hostiles cargados en la base de datos de demostración.

### Vectores revisados

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Sinks de HTML dinámico (AM-701) | Sin `v-html`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, `javascript:`, `data:` en atributos (ESLint + test propio) | **Verificado** por búsqueda en todo el código y por 15 tests de fuente; el único `:href` es una constante hacia el login |
| XSS almacenado en el navegador | Todo texto de origen externo se interpola como texto y pasa por `visibleText` (bidi, ancho cero, ANSI, recorte) | **Verificado en Chrome** con HTML hostil (`<script>`, `<img onerror>`, `<svg onload>`, `<iframe src=javascript:>`, `<a href=javascript:>`, `<style>`, entidades) en título, ruta, mensaje, snippet y log: se muestra literal; 0 elementos inyectados, `window.__pwn` sin definir, el `<style>` no se aplica (la página sigue visible), sin manejadores en línea ni enlaces `javascript:` |
| Logs enormes o con secuencias de control | `visibleText` recorta a 2000 caracteres | **Verificado**: una línea de 4000 caracteres se muestra recortada (máx. 2028 con el aviso `…[recortado]`); el backend limita además la columna a 4096 |
| CSP (AM-705) | `default-src 'none'`, scripts, estilos, imágenes y conexiones solo del mismo origen; sin `unsafe-inline`/`unsafe-eval` | **Verificado en Chrome**: sin ningún mensaje de consola ni violación en las 8 rutas con cargas completas; `index.html` sin scripts ni estilos en línea (test) |
| Trusted Types | `require-trusted-types-for 'script'` | **Activado** (F7-23). Comprobado que el navegador rechaza `innerHTML` (`TrustedHTML` requerido) y que **las 8 páginas, la navegación interna y el modal funcionan igual** (mismo contenido, consola limpia): la aplicación no usa ningún sink de HTML. Un test de despliegue lo exige |
| Rutas (AM-706) | Lista cerrada con UUID en el patrón de la ruta; sin `next`/`redirect` | **Verificado en Chrome**: `/organizations/not-a-uuid` → «No encontrado»; con sesión, `/login?next=https://evil.example/&redirect=//evil.example&returnTo=javascript:1` → `/` en el mismo origen y sin consulta; `//evil.example/path` → «No encontrado» en `localhost`; `..%2f..%2fapi…` → 400 de Nginx; un UUID en mayúsculas se acepta y se normaliza |
| Token CSRF y sesión | CSRF solo en memoria (cierre fuera de Pinia), cookie `HttpOnly`, sin almacenamiento web | Verificado por tests (no hay `localStorage`/`sessionStorage`/`indexedDB`/`document.cookie`) y por el componente 5 |
| Cliente HTTP | Solo `/api/v1`, parámetros de ruta UUID, consulta cerrada, `redirect: "error"`, `no-referrer`, tiempo de espera, respuesta solo `application/json` | Correcto por lectura y tests (`http.test.ts`, `endpoints.test.ts`) |
| Errores del servidor | Solo `title` acotado y `request_id` con formato fijo | Correcto por lectura: texto del servidor nunca se interpreta |
| Enlaces con identificadores del servidor | Solo si son UUID (`isUuid`) | Test de fuente que lo exige en todo `.vue` con `params` |
| Dependencias | 3 de producción (`vue` 3.5.43, `vue-router` 5.4.0, `pinia` 4.0.3) y 12 de desarrollo, todas con versión exacta; `.npmrc` con `ignore-scripts=true`, `save-exact`, `audit-level=high` | **Verificado**: 301 paquetes en el árbol, 0 vulnerabilidades (producción y desarrollo), el único script de instalación es el de `fsevents` (macOS, opcional) y queda desactivado; ver F7-22 |

### F7-22 — Lockfile sin hashes

- **Evidencia:** `package-lock.json`: 301 paquetes, 37 con `integrity` y `resolved`; 264 solo con versión. `npm ci` solo verifica el tarball de lo que tiene hash.
- **Corrección:** se consultó al registro (`registry.npmjs.org`) la versión exacta de cada paquete sin hash y se rellenaron `resolved` e `integrity` **sin tocar ninguna versión** (0 cambios, comprobado paquete a paquete); `undici` se subió dentro del rango de `jsdom` para cerrar la vulnerabilidad. Se verificó con `npm ci` (falla si un hash no coincide), 238 tests, `vue-tsc`, ESLint, build y `npm audit` en 0.
- **Regresión:** `frontend/tests/security/lockfile.test.ts` (4 tests): formato con hashes, SHA-512 en todos los paquetes, descarga solo desde el registro público por HTTPS y dependencias directas con versión exacta.
- **Pendiente de operación:** que el CI use el lockfile nuevo (ya hace `npm ci` y `npm audit --audit-level=high`) y que Dependabot/Renovate lo mantenga.

### Observaciones sin riesgo

- El dominio **se niega a cargar** un finding cuyo título lleve caracteres de control o bidi, o cuyo `rule_id` incumpla su patrón, y responde 400 en esa petición (y en la lista que lo contenga). Es correcto como defensa (esos datos solo podrían llegar saltándose el normalizador, como hizo mi inserción de prueba por SQL), pero un único registro corrupto rompería el listado completo de un repositorio; una mejora posible es omitirlo y marcarlo como dañado.
- El aviso de certificado autofirmado de `https://localhost` y de `https://keycloak.localhost:8444` es propio del perfil de desarrollo; el navegador de la extensión no puede pulsarlo y hubo que aceptarlo a mano.
- La superposición de la extensión de Chrome inserta dos `svg` en el documento (fuera de `main`); no pertenecen a la aplicación.

### Nota de superficie de ataque

- Entradas no confiables: todo texto de findings, rutas, mensajes, snippets, logs, nombres de organización, repositorio y usuario, y los identificadores de la URL.
- Mitigaciones verificadas: texto siempre interpolado y normalizado, CSP estricta con Trusted Types, rutas cerradas, cliente HTTP restringido, sesión y CSRF fuera del alcance de JavaScript de terceros, cadena de suministro con hashes y sin vulnerabilidades conocidas.
- Residual: ninguno nuevo; la dependencia de `jsdom`/`undici` es de desarrollo y no se distribuye.

## C6 — Autorización y multi-tenancy (TB1, AM-106/107)

Revisado: `application/services/authorization.py`, todos los casos de uso con escritura (`manage_membership`, `triage_finding`, `start_scan`, `cancel_scan`) y de lectura (`browse`, `queries`), `ScanScheduler`, `persistence/repositories.py` y `errors.py`, y el catálogo de 13 operaciones REST con su rol mínimo. Verificado en vivo con **seis usuarios** (admin, segundo admin, triager y viewer de `demo-corp`; admin de `other-corp`; usuario sin organización) y sesiones creadas a propósito, a través de Nginx.

### Matriz en vivo (13 operaciones × 6 usuarios × 3 destinos)

| Destino | Celdas | Discrepancias |
|---|---|---|
| Recursos de la propia organización (rol suficiente → éxito, insuficiente → 403, ajeno → 404) | 78 | 0 |
| Recursos de la otra organización (solo su admin accede; todos los demás 404) | 78 | 0 |
| Identificadores inexistentes (todo 404) | 78 | 0 |

- Los 404 de un recurso ajeno y de uno inexistente son **indistinguibles** (mismo cuerpo salvo `request_id`) en las 13 operaciones.
- Escalada: un viewer no puede subirse a ADMIN ni un triager retirar a un admin (403, rol intacto en la base de datos); el admin de otra organización no ve la membresía de ésta (404) y un admin de `demo-corp` no puede tocar la de `other-corp` (404, nada se crea).
- Entradas hostiles en el cambio de rol: campos extra (`organization_id`), rol inexistente, minúsculas, nulo, numérico, ausente o con espacios → 400.
- Último administrador: degradarse o retirarse siendo el último → 409; degradar a otro con más de un admin → permitido.
- Listados: `seen_in_scan` con un escaneo de otra organización → 404; un `cursor` con el id de un finding ajeno no filtra contenido ajeno; `GET /organizations` devuelve solo las organizaciones propias (viewer: `demo-corp`; otto: `other-corp`; sin membresías: vacío).
- Triage de un finding de otra organización por un triager ajeno → 404 sin efecto (la única decisión registrada en el finding de `other-corp` es la de su propio admin).

### F7-14 — Carrera entre administradores

- **Corrección:** `lock_admins` (`SELECT … FOR UPDATE` sobre las filas de ADMIN, orden fijo, sin deadlocks) se toma antes de decidir si quitar o degradar a un administrador, y el rol se relee tras esperar el bloqueo; promocionar o tocar a no admins no lo necesita.
- **Verificación:** en vivo, 5 iteraciones con dos admins retirándose a la vez → **0 organizaciones sin administrador** (el que llega segundo recibe 409; antes 4 de 5 quedaban vacías). Tests: `test_membership_concurrency.py` (PostgreSQL real: bloqueo observable entre dos transacciones y 12 carreras con barrera) y dos unitarios que comprueban cuándo se toma el bloqueo. Comprobado con un mutante (volver a `count_admins`): los tests fallan.

### F7-15 — Escaneos que no se podían sustituir

- **Evidencia:** con huecos de 0 y 0,05 s entre dos `startScan` de la misma rama, el segundo recibía 404 en 4 de 4 intentos y no se creaba su escaneo; con 0,3 s o más, no (el primero ya había terminado). Pila obtenida ejecutando la aplicación real en el contenedor: el `NotFoundError` nace en `translate_database_error` al cerrar la unidad de trabajo.
- **Corrección:** se leen los escaneos activos antes de insertar el nuevo, se inserta el nuevo y solo después se marcan los antiguos como sustituidos. El doble de pruebas replica ahora la FK (`_check_superseded_by`), de modo que el test unitario existente habría fallado antes de la corrección.
- **Verificación:** test de integración con PostgreSQL real (`test_a_new_scan_of_the_same_ref_supersedes_the_pending_one_on_postgres`); mutante (volver al orden anterior) → fallan el unitario y el de integración; en vivo, `startScan` del admin inmediatamente después de otro usuario → 202.
- **Impacto en producción:** la mitigación AM-203 («un push nuevo sustituye al pendiente») no funcionaba, y con escaneos reales de minutos un push durante un escaneo habría fallado su webhook (404 → entrega fallida de GitHub) y dejado ese commit sin escanear.

### F7-16 — Inundación de la auditoría por no miembros

Ver ADR 0026: los accesos denegados a un miembro (403) siguen en el audit log; los sondeos de no miembros (404) pasan a un aviso estructurado `nave.authz`. En vivo: tras una pasada completa de la matriz el recuento de filas `access.denied` de no miembros no varió (119, todas anteriores a la corrección) mientras aparecían 119 avisos nuevos en el log de la API, y las denegaciones de miembros subieron de 12 a 24. Como efecto secundario, una petición de un no miembro ya no hace ninguna escritura, lo que reduce la diferencia de tiempo entre «ajeno» e «inexistente». Residual: los logs no son inmutables (RR-30).

### Observaciones sin riesgo

- La cadena de hash del audit log sigue verificándose en los tests de integración (`verify_audit_chain`).
- El rol de un usuario retirado de una organización deja de valer en la siguiente petición (se comprueba en cada caso de uso); la sesión no se invalida, solo pierde acceso a esa organización.
- Un administrador puede conceder roles a cualquier usuario que haya iniciado sesión alguna vez (por su UUID); solo ve los UUID de los miembros de su organización.
- `dev_demo.ps1` da el rol ADMIN al **último usuario que inició sesión**: si hay otros usuarios recientes (como los de pruebas), la demo puede quedar a nombre de otro.

### Nota de superficie de ataque

- Entradas no confiables: identificadores de organización, repositorio, escaneo, finding y usuario en rutas y parámetros; cuerpos de cambio de rol y de triage.
- Mitigaciones verificadas: autorización en cada caso de uso con 404 uniforme para lo ajeno, 403 solo para miembros, validación estricta de cuerpos, regla del último admin segura bajo concurrencia, escritura de escaneos coherente con las claves foráneas y auditoría acotada a eventos de miembros.
- Residual: avisos de sondeo solo en logs (RR-30).

## C7 — Persistencia, PostgreSQL, Redis y Celery (TB3)

Revisado: `deploy/postgres/init-roles.sh`, las 6 migraciones de Alembic (privilegios, triggers y funciones `SECURITY DEFINER`), `persistence/engine.py` y `errors.py`, `deploy/redis/entrypoint.sh`, `infrastructure/queue/celery_app.py` y los secretos montados en cada servicio. Verificado en vivo con las credenciales reales de cada rol y desde dentro de la red.

### PostgreSQL

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Roles | `nave_app`, `nave_maintenance` y `nave_migrator` sin superusuario, `CREATEDB`, `CREATEROLE`, replicación ni `BYPASSRLS`; propietario de esquema y tablas: `nave_migrator` | **Verificado** en `pg_roles` y `pg_class` |
| Autenticación | `scram-sha-256`; `pg_hba` con `trust` solo por socket local del propio contenedor | **Verificado** (`pg_hba_file_rules`); sin TLS (F7-20) |
| Mínimo privilegio de `nave_app` | Sin `UPDATE`/`DELETE`/`TRUNCATE` sobre `audit_log` y `triage_decisions`; sin `DELETE` sobre `findings` ni `scans`; sin DDL | **Verificado**: 27 operaciones prohibidas bloqueadas por privilegios: `UPDATE`/`DELETE`/`TRUNCATE audit_log`, `UPDATE triage_decisions`, `DELETE findings`/`scans`, `DROP`/`ALTER TABLE`, `CREATE TABLE`/`FUNCTION` en `public`, tablas temporales, `CREATE ROLE`, `ALTER ROLE … SUPERUSER`, `SET ROLE` a los otros roles, `GRANT` sobre `audit_log` (sin efecto), `COPY … TO PROGRAM`, `pg_read_file`, `lo_import`, `CREATE EXTENSION`, `pg_authid`, `pg_shadow` |
| Mínimo privilegio de `nave_maintenance` | Solo `SELECT, DELETE` en tablas de retención y `SELECT` en `repositories`/`organizations`/`findings` | **Verificado**: no puede borrar `audit_log`, `findings`, `repositories`, `triage_decisions` ni `users`, no puede actualizar `users`, no ve ni escribe `sessions`, no puede crear tablas ni cambiar de rol |
| Protección de solo-añadir | Triggers `audit_log_append_only`, `audit_log_no_truncate` y `triage_decisions_immutable` | **Verificado** que existen y están activos; en la práctica el privilegio ya bloquea antes (defensa en profundidad). Solo el propietario (`nave_migrator`, secreto montado únicamente en el trabajo de migración) podría desactivarlos |
| Funciones `SECURITY DEFINER` | `search_path` fijo (`pg_catalog, public`), `REVOKE ALL … FROM PUBLIC`, `EXECUTE` solo para `nave_maintenance` | **Verificado** en `pg_proc` (`prosecdef`, `proconfig`, `proacl`); `nave_app` → permiso denegado en ambas |
| `nave_purge_organization` | El plazo de 30 días y el estado `UNINSTALLED` se exigen **dentro** de la función | **Verificado**: purgar `demo-corp` (activa) → «organization is not purgeable»; organización inexistente → `absent`; `p_batch` negativo se acota |
| `nave_scrub_orphan_users` | Suelo de 30 días dentro de la función | **Verificado**: con una fecha 10 años en el futuro o `NULL` no se amplía el alcance |
| Tiempos | `statement_timeout` 15 s e `idle_in_transaction_session_timeout` 60 s por conexión, fijados por la aplicación | Correcto por lectura (`engine.py`); el servidor tiene 0, pero la aplicación lo impone en cada conexión. Una espera de bloqueo (p. ej. `lock_admins`) queda acotada por el mismo `statement_timeout` |
| Visibilidad de otras sesiones | Sin `pg_read_all_stats` | **Verificado**: `nave_app` no ve el texto de las consultas de otros usuarios |

### Redis

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Autenticación | Contraseña (solo su hash en la configuración), usuario `default` | **Verificado** desde dentro de la red: sin autenticar → `NOAUTH`; contraseña errónea → `WRONGPASS` |
| Comandos administrativos | `-@admin -flushall -flushdb`; `DEBUG` y `MODULE` desactivados en el servidor | **Verificado**: `CONFIG`, `FLUSHALL`/`FLUSHDB`, `SHUTDOWN`, `DEBUG`, `ACL`, `REPLICAOF`, `MODULE`, `SAVE`/`BGSAVE`, `MONITOR`, `CLIENT KILL` bloqueados (15 de 17 pruebas; las 2 restantes eran `FUNCTION LOAD` y `SCRIPT FLUSH`, ver F7-18) |
| Persistencia | Sin disco (`save ""`, `appendonly no`) | **Verificado** (`INFO persistence`: `aof_enabled:0`, `maxmemory 256 MB`, `noeviction`) |
| Alcance de red | Solo la red `internal` | **Verificado** en C3: `api`, `worker`, `scheduler`, `postgres` y `redis` en `internal`; el runner y el frontend no resuelven `redis` |

### Celery

| Vector | Mitigación comprobada | Resultado |
|---|---|---|
| Deserialización insegura (AM-304) | JSON únicamente (`accept_content`) | **Verificado en vivo**: un mensaje **pickle** sobre una tarea registrada llegó a la cola y el worker respondió «Refusing to deserialize untrusted content of type pickle (application/x-python-serialize)» y lo descartó sin ejecutar la tarea |
| Tareas inexistentes | `task_create_missing_queues=False`, solo tareas registradas | **Verificado**: un JSON con una tarea que no existe → «Received unregistered task … discarded» |
| Entrega | `acks_late`, `reject_on_worker_lost`, sin resultados, sin eventos | Correcto por lectura; cola `maintenance` sin mensajes colgados tras las pruebas (`LLEN 0`, `unacked 0`) |
| Control remoto (RR-09) | ACL y red `internal` | **Verificado** que **la API puede inspeccionar el worker** a través de Redis (`ping`, `registered`) con su propio secreto: cualquier componente con acceso a Redis tiene el mismo poder (`shutdown`, `revoke`, `pool_restart`). La API necesita `revoke` para sustituir escaneos |
| Planificación | Beat en memoria, sin `shelve` | **Verificado**: 6 tareas periódicas definidas; con la ACL actual no hay errores de permisos en `api`, `worker` ni `scheduler` |

### Reparto de secretos (comprobado con `docker inspect`)

| Servicio | Secretos montados |
|---|---|
| `api` | `db_app_password`, `redis_password`, `webhook_secret`, `oidc_client_secret` (+ `tls_cert` solo en dev) |
| `worker` | `db_app_password`, `db_maintenance_password` (RR-10), `fingerprint_key`, `github_app_private_key`, `redis_password`, `runner_hmac_key` |
| `scheduler` | solo `redis_password` |
| `scan-runner` | solo `runner_hmac_key` |
| `frontend` | `tls_cert`, `tls_key` |
| `egress-proxy`, `redis` | ninguno / solo `redis_password` |
| `postgres` | las cuatro contraseñas de base de datos (solo se necesitan en la primera inicialización) |

La API no tiene la clave de GitHub, la del runner ni la de mantenimiento; ningún servicio recibe credenciales incrustadas en una URL de su entorno (`NAVE_*_FILE` en todos los casos). `NAVE_MIGRATOR` solo existe en el trabajo de migración.

### F7-18 — Redis: qué se logró y qué no

El primer intento fue denegar todo el scripting (`-@scripting`). **Rompió el worker**: tras recrear Redis, el worker registró `CRITICAL Unrecoverable error: auth:NoPermissionError` con la pila en `redis/lock.py → release`, porque `Lock.release` de redis-py usa Lua y kombu lo emplea en su mutex de reentrega. Se detectó porque se probó de extremo a extremo (API → Redis → worker → base de datos) antes de dar el cambio por bueno, y se revirtió. La regla final deniega solo lo que nadie usa y es persistente: `FUNCTION`, `FCALL`, `FCALL_RO`. `EVAL`/`EVALSHA`/`SCRIPT LOAD` siguen permitidos. Verificado tras recrear Redis: el mutex (`lock.acquire/release`) funciona, `FUNCTION LOAD` y `FCALL` están bloqueados, un escaneo se encola, la sustitución del pendiente revoca por Redis (`CANCELLED|SUPERSEDED`) y el worker consume el siguiente; sin errores de permisos en los logs tras dos minutos. Test estático: `test_redis_no_expone_la_contrasena` comprueba las denegaciones y que `-@scripting` **no** esté presente.

Mejora pendiente (RR-31): usuarios de Redis distintos para la API (solo publicar y difundir órdenes), el worker (consumir) y el planificador (publicar), cada uno con la lista mínima de comandos que se determine con una prueba de integración con Redis real (en CI hay un test que se omite sin `redis-server`).

### F7-19 — Conexión a bases de sistema

`REVOKE CONNECT ON DATABASE postgres, template1 FROM PUBLIC` en `init-roles.sh` lo cerraría, pero solo se ejecuta al crear el volumen. Para despliegues ya existentes, aplicarlo a mano con el superusuario. Sin riesgo funcional: la aplicación solo usa la base `nave`.

### Nota de superficie de ataque

- Entradas no confiables: todo lo que un componente comprometido (API, worker) puede enviar a PostgreSQL y a Redis con sus credenciales; mensajes en las colas; parámetros de las funciones de mantenimiento.
- Mitigaciones verificadas: roles sin privilegios con permisos mínimos, auditoría y decisiones de triage no reescribibles, plazos exigidos dentro de las funciones `SECURITY DEFINER`, JSON como único formato aceptado por Celery, Redis sin comandos administrativos ni funciones persistentes, secretos repartidos por necesidad.
- Residual: un componente comprometido controla las colas y el worker vía Redis (RR-09, RR-31) y el tráfico interno no está cifrado (RR-32).

## C9 — Despliegue y cadena de suministro (TB9)

### Vectores revisados

| Vector | Comprobación | Resultado |
|---|---|---|
| Acciones de CI mutables | Las 10 acciones están fijadas por SHA; comprobado contra la API de GitHub que cada SHA corresponde a la etiqueta comentada | 10 de 10 coinciden |
| Imágenes sin digest | `pin_images.py` → `deploy/images.lock.env`; plantilla de producción sin valores por defecto | Corregido (F7-24, F7-25) |
| Binarios de escáner | `check_scanner_flags.py` contra los binarios reales: 15 flags de Semgrep y 13 de Trivy | Cierra RR-12 |
| Compose endurecido | `docker inspect` de los 9 contenedores en ejecución (tabla siguiente) | Conforme salvo Keycloak (solo perfil `dev`) |
| Secretos | Solo ficheros bajo `/run/secrets`; sin `PASSWORD`/`KEY` en claro en el entorno, solo variables `*_FILE`; el runner solo recibe la clave HMAC | Conforme |
| Puertos publicados | Solo `frontend` (80/443); Keycloak solo en `127.0.0.1:8444` en dev; postgres, redis y proxy sin puertos | Conforme |
| Autoescaneo | Semgrep (reglas de la plataforma) y Trivy fs (vuln, secret, misconfig; HIGH/CRITICAL) sobre el repositorio | 0 hallazgos tras F7-26 |
| Vulnerabilidades de imágenes | `trivy image` sobre las 7 imágenes construidas, con `--ignore-unfixed` | backend, runner y fetcher limpias; el resto, F7-27 y F7-28 |
| Firma y SBOM | Lectura de `release.yml`: buildx, SBOM CycloneDX, cosign sin clave; no ejecutado (requiere publicar en el registro) | Pendiente de prueba real |

Endurecimiento observado (`docker inspect`): todos los servicios propios con `read_only`, `cap_drop: ALL`, `no-new-privileges`, límite de memoria, CPU y PIDs, `restart: unless-stopped` y healthcheck; usuario no root (101, 999, 65532). PostgreSQL arranca como root dentro del contenedor solo para que su entrypoint haga `gosu` a `postgres`, con cinco capacidades añadidas (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`) y nada más. Keycloak (solo `dev`) no tiene FS de solo lectura, healthcheck ni límite de PIDs: no se despliega en producción.

### F7-24 a F7-28

Ver el registro. Lo más relevante: el CI de la plataforma parecía verde sin construir nada con digests (F7-25) y la imagen del proxy de salida, pieza clave de TB8, no tenía forma de llegar a producción (F7-24). Ambos se detectaron al reproducir el pipeline en local, no al leer los ficheros.

### Observaciones sin riesgo

- El socket del motor de contenedores se monta solo en `scan-runner`, por diseño (ADR 0002); en producción es el socket de un motor rootless con gVisor.
- El worker recibe `db_maintenance_password` porque ejecuta las tareas de mantenimiento (ADR 0011); la API no.
- La excepción de Trivy cubre una sola ruta; cualquier otro secreto sigue bloqueando el merge.

### No verificado

- Firma cosign y SBOM en una ejecución real de `release` (necesita registro y permisos de GitHub).
- `docker compose config` con un `images.env` de producción con digests propios (los de las imágenes propias dependen de la primera publicación).
- Escaneo de las imágenes por CI tras publicar; aquí se escanearon las imágenes `:dev` locales.

### Nota de superficie de ataque

- Entradas no confiables: el contenido de los registros de imágenes y paquetes (npm, PyPI, Go) y las acciones de terceros.
- Mitigaciones verificadas: digests y hashes en todos los artefactos, acciones por SHA, lockfiles con integridad, versiones de componentes compilados fijadas, Dependabot, autoescaneo bloqueante, tests que impiden volver atrás (usuario no root, imágenes publicadas, excepción de secretos mínima).
- Residual: los CVE de paquetes base se arreglan al refrescar digests, no automáticamente (RR-33); la dependencia directa de Smokescreen (`v0.1.0`) y sus elevaciones manuales requieren revisión al actualizar.

## C10 — Secretos y logs (transversal)

### Vectores revisados

| Vector | Comprobación | Resultado |
|---|---|---|
| Secretos en los logs de los 9 contenedores | Búsqueda de tokens `gh*_`, JWT, claves privadas, `password=`, cookies, `Authorization` en los registros acumulados | 0 coincidencias |
| Secretos enviados por el atacante hacia los logs | Sonda en vivo: token en cabecera `Authorization`, cookie de sesión, consulta (`?token=&password=`), callback OIDC con `code`/`state`, cuerpo de webhook, ruta y User-Agent con el valor | Nginx y API registran solo método y ruta; la consulta nunca se registra; el valor en la ruta salía sin redactar en la API (F7-29) |
| Inyección de líneas | `%0a`, `%0d%0a` y `%22` en la ruta | Nginx escapa (`\x0A`); gunicorn escribía el salto real (F7-29, corregido y repetido en vivo) |
| Registros JSON de la aplicación | Lista cerrada de campos, mensaje de excepción y variables locales nunca registrados, `error` solo con código o nombre de tipo (revisados todos los puntos de registro) | Conforme; huecos menores en la redacción (F7-30) |
| Representación de objetos con secretos | `repr=False` en claves privadas, claves HMAC, URL de base de datos, secreto de OIDC, token de instalación, URL del broker, material de fingerprint, secretos de webhook | Conforme |
| Secretos en entorno e imágenes | `docker inspect`: solo variables `*_FILE`; ficheros en `/run/secrets`; la clave HMAC del runner es el único secreto que ve `scan-runner`; autoescaneo sin secretos salvo el token ficticio del vector firmado | Conforme |
| Token de clonado | Llega al fetcher por stdin y viaja a git en una variable de entorno, no en la línea de órdenes (tests existentes); el runner no registra cuerpos de petición (`log_message` anulado) | Conforme |

### F7-29 y F7-30

Ver el registro. El primero es un caso típico de "el control estaba en un sitio y faltaba en el vecino": el formateador JSON y Nginx saneaban, el logger de acceso de gunicorn no.

### No verificado

- Los logs de Keycloak (solo `dev`) y de PostgreSQL no se han revisado más allá de la búsqueda de patrones; PostgreSQL registra conexiones (`log_connections`) pero no sentencias.
- Los logs de gunicorn de arranque y los de Celery no pasan por el formateador JSON; no contienen entrada de usuario.
- `mypy` sobre `tests` muestra 50 errores en el contenedor de pruebas (ninguno en `src`, que está limpio con el módulo nuevo); no se han investigado y conviene comprobarlos en el primer CI real.
- Un escaneo real con GitHub App (token de instalación en tránsito) queda para la prueba final.

### Nota de superficie de ataque

- Entradas no confiables que llegan a los logs: ruta, método, User-Agent y cabeceras de la petición, texto de reglas y rutas de ficheros escaneados, mensajes de error de dependencias.
- Mitigaciones verificadas: registro por lista cerrada de campos, redacción antes del recorte, escape de caracteres de control en los tres emisores de acceso (Nginx, gunicorn, JSON), consulta fuera de los logs de acceso (`code` y `state` de OIDC), excepciones sin mensaje ni variables.
- Residual: un secreto con formato desconocido en un mensaje libre no se redacta; por eso los puntos de registro usan códigos y no texto de terceros.

## C11 — Funciones posteriores a la revisión (ADR 0027–0030)

Revisión delta del 2026-10-28 sobre lo añadido con el rediseño de la interfaz: vistas agregadas por organización (ADR 0028), cola de triage, exportación CSV/SARIF y sesiones propias (ADR 0029), administración de plataforma y edición de la política (ADR 0030). ADR 0027 es solo visual.

### Vectores revisados (en vivo contra la pila reconstruida)

| Vector | Comprobación | Resultado |
|---|---|---|
| `/platform/*` sin el rol | 5 rutas (lectura y escritura) con un usuario sin rol de plataforma | 404 en todas, sin efectos |
| El rol de plataforma como llave a datos | `stats`, exportación y hallazgos de demo-corp con un administrador de plataforma sin pertenencia | 404 |
| Autoasignación de rol | `PUT /platform/users/{propio}/memberships/{org}` con `ADMIN` | **204 y acceso al audit log antes de corregir (F7-31)**; 403 después |
| CSRF en escrituras de plataforma | `PUT` sin `X-CSRF-Token` | 403 |
| Inyección de fórmulas en CSV | Hallazgo con título `=HYPERLINK(...)` exportado como VIEWER | Celda prefijada con `'`; `Content-Disposition` con nombre fijo y `Cache-Control: no-store` |
| Exportación entre organizaciones | Filtro `repository_id` de otra organización, exportación de repositorio y de organización ajenos | 0 filas, 404 y 404 |
| SARIF exportado | Mismo hallazgo hostil | El título no se incluye (el escritor usa los metadatos de la regla) |
| Vistas de gobierno | Audit log y edición de la política como VIEWER; cursor manipulado en la cola de triage | 403, 403 y 400 |
| Sesiones propias | Cerrar la sesión de otro usuario con su identificador público calculado; listar; cerrar la actual por esta vía | 404, solo las propias, 400 |
| Contadores de webhooks | Lectura: acumulación en memoria acotada (240 claves), volcado como mucho cada 30 s y un fallo de escritura no tumba la respuesta | Conforme |
| Política | `max_length` en el modelo HTTP y `MAX_GATES` en el dominio; versiones nuevas sin reescribir | Conforme (tests existentes) |

### Observaciones sin riesgo

- El administrador de plataforma ve nombre y correo de todos los usuarios: es la función pedida y no da acceso a datos de organizaciones.
- La CLI que concede el rol usa la credencial `nave_app` (trade-off documentado en el ADR 0030).

### Nota de superficie de ataque

- Entradas no confiables: identificadores de usuario y organización en la ruta, cuerpos de política, filtros y cursores de las vistas, títulos y justificaciones exportados.
- Mitigaciones verificadas: autorización en cada caso de uso (404 para no miembros y no administradores de plataforma, 403 por rol), CSRF en escrituras, neutralización de fórmulas, filtros restringidos a la organización en la consulta, sesiones solo del propio usuario, prohibición de autoasignarse roles.
- Residual: un administrador de plataforma puede dar un rol a otra cuenta que controle (RR-34); queda auditado en la organización afectada.
- Las sondas dejaron en el audit log de demo-corp 3 entradas `membership.changed` (`via=platform`, antes de la corrección) y 1 `access.denied` (`reason=self`); el audit log es inmutable. Los usuarios, sesiones y el hallazgo de prueba se retiraron.

## C12 — Escaneo real con una GitHub App (2026-10-28)

Prueba de extremo a extremo con GitHub real, sin coste: GitHub App `nave-dev-carlos1219` creada por el flujo de manifiesto (permisos `contents:read`, `metadata:read`, `checks:write`, `pull_requests:write`, `security_events:write`; eventos `push` y `pull_request`), instalada **solo** en el repositorio privado de prueba `carlos1219/nave-prueba`, con los webhooks entrando por un túnel rápido de Cloudflare hacia el Nginx local.

### Resultado

| Paso | Resultado |
|---|---|
| Webhook sin firma por el túnel | 401 |
| Evento de instalación firmado | 202; organización `carlos1219` creada y repositorio sincronizado desde la API de GitHub a través del proxy de salida |
| PR con código vulnerable (`eval`, `subprocess` con `shell=True`, `pickle.loads`) y dependencias antiguas | Primeros intentos fallidos: F7-32 y F7-33. Tras corregirlos: escaneo `COMPLETED` en 13 s |
| Hallazgos | Semgrep 3 (2 HIGH, 1 MEDIUM) y Trivy 20 (2 CRITICAL, 8 HIGH, 10 MEDIUM) |
| Informe en GitHub | Check run «NAVE» y comentario en la PR con el veredicto y los hallazgos nuevos |
| Salida a Internet | Solo `api.github.com` (68 conexiones) y `github.com` (12) a través de `egress-proxy` |

Los dos fallos encontrados (F7-32 bloqueante, F7-33 alto) impedían cualquier escaneo real y no los detectaba ninguna suite: es exactamente el tipo de problema que esta prueba debía sacar a la luz.

### Observaciones

- **Veredicto NEUTRAL con 2 CRITICAL:** la organización no tiene política de quality gates y sin política el resultado es informativo. Para que una PR falle hay que publicar una política (vista de gobierno, ADR 0030). Conviene decidir si una organización nueva debe recibir una política por defecto.
- **Doble escaneo:** un `push` a la rama de una PR abierta genera dos escaneos del mismo commit (rama y `refs/pull/N/head`).
- **SARIF a Code Scanning:** en un repositorio privado sin GitHub Code Security la subida no está disponible; no se verificó.
- La primera clave privada de la App (la del manifiesto) se perdió sin salir de la máquina cuando falló su escritura; sigue registrada en GitHub y conviene borrarla desde la página de la App.

### Nota de superficie de ataque

- El túnel expuso la API local a Internet durante la prueba; el webhook exigió firma y el resto exige sesión. Se cerró al terminar.
- Los secretos de la App (clave privada y secreto del webhook) no pasaron por formularios ni por el chat: la clave se movió de Descargas a `deploy/secrets` y se borró la copia; el secreto se fijó por la API con el JWT de la App.

## C13 — Primer CI real en GitHub Actions (2026-10-31)

Repositorio privado `carlos1219/nave`, Actions limitado a acciones de GitHub y a una lista
cerrada de terceros (`anchore/sbom-action`, `aquasecurity/setup-trivy`, `aquasecurity/trivy-action`,
`docker/*`, `sigstore/cosign-installer`), con SHA completo obligatorio, token de workflow de solo
lectura y sin ejecución desde forks.

| Intento | Resultado | Causa y corrección |
|---|---|---|
| Local, antes de activar Actions | mypy fallaba con 50 errores en `tests/`; un test de integración de Celery/Redis construía `MaintenanceTaskDependencies` sin dos argumentos; el test e2e importaba el runner, que el job no instalaba | Tipos corregidos, test arreglado, `py.typed` en `nave-runner` e instalación del runner en el job del backend |
| CI #1 | `deploy-static` y `self-scan` en rojo | `pip install --require-hashes -r ... pytest==9.1.1` mezclaba un paquete sin hash (pip lo rechaza): pytest pasa al lock con hashes y un test lo impide. `trivy-action` usa `aquasecurity/setup-trivy`, que faltaba en la lista permitida |
| CI #2 | **Los 6 jobs en verde** (≈ 4 min) | — |

Aviso pendiente: `docker/setup-buildx-action` apunta a Node 20 (obsoleto); lo actualizará Dependabot.
No verificado: el workflow `release` (cosign, SBOM, GHCR).

