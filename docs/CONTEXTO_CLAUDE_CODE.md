# NAVE — contexto de traspaso para Claude Code

> Léelo entero antes de tocar nada. Después lee `CLAUDE.md` (fuente de verdad), `docs/architecture/despliegue.md` y los ADR 0013–0023. Idioma: documentación y conversación en español; identificadores en inglés.

## 1. Qué es NAVE
Plataforma SAST/SCA (escáner estático de código y de dependencias). Backend Python 3.12 (Flask + Celery + Postgres, arquitectura hexagonal), frontend Vue 3 + TypeScript estricto, escaneos en un `scan-runner` aislado que lanza Semgrep y Trivy en contenedores. Se integra con GitHub mediante una GitHub App.

## 2. Reglas de trabajo (de CLAUDE.md)
- Diseño antes que código; código de producción con tests; ADR para toda decisión estructural.
- §13 visual (ahora «interfaz empresarial moderna», ADR 0023), §14 reglas del frontend, §15 infraestructura, §22 antipatrones.
- El responsable delega las decisiones: «lo más seguro y lo que menos problemas dé». Documenta lo que decidas.
- Nunca inventes que algo está verificado: di qué se ha probado y qué no.

## 3. Estado por fases
| Fase | Estado |
|---|---|
| Backend (ADR 0013–0020: límites de capa con import-linter, rate limiter, ProxyFix, funciones SQL `SECURITY DEFINER` de mantenimiento, OIDC con proxy de salida, sesiones en Postgres con CSRF, contrato OpenAPI en `contracts/openapi.json`) | Hecho y con tests |
| Frontend F0–F7 (ADR 0021: Vue 3.5, Vite, Pinia solo para sesión, Feature-Sliced con reglas ESLint, cliente API generado, CSRF solo en memoria, rutas solo con UUID, sin `v-html`, CSP estricta) | Hecho; 233 tests |
| Despliegue, fase 6 (ADR 0022: Compose endurecido, Nginx, CI, firma/SBOM) | Escrito; **primer arranque real hecho en Docker Desktop (dev)** |
| Rediseño visual (ADR 0023) | Hecho; revisado con capturas en claro y oscuro |
| **Fase 7: revisión de seguridad final de todo el sistema** | **Pendiente** |

## 4. Estado del despliegue local (Windows + Docker Desktop, perfil `dev`)
Repo en `C:\Users\Usuario\Desktop\NAVE`. Los 9 contenedores llegaron a estar arriba (postgres, redis, keycloak, api, worker, scheduler, scan-runner, egress-proxy, frontend). El login con Keycloak (`dev`/`dev`) funciona en https://localhost.

Scripts (PowerShell, desde la raíz):
- `deploy/scripts/dev_up.ps1`: construye imágenes (`docker-compose.build.yml` + `deploy/images.dev.env`, etiquetas `nave-*:dev`), calcula el digest de las imágenes de escáner, migra y levanta. Se detiene en el primer fallo.
- `deploy/scripts/dev_logs.ps1`: vuelca estado y logs a `deploy/dev-logs.txt` (ignorado por git; salida UTF-8 con BOM).
- `deploy/scripts/dev_demo.ps1 [-Remove]`: carga/retira datos demo (`deploy/dev/demo_data.sql`, marca `github_installation_id = 999000001`, organización `demo-corp`). **Escrito y probado contra Postgres real en mi entorno, pero el usuario aún no lo ha ejecutado en su Docker.**

Generados en local (no versionados): `deploy/secrets/*` (incluye `github_app_private_key` **marcador**, no válida), `deploy/.env`, `deploy/images.dev.env` (sin digests, solo dev).

## 5. Qué se arregló al arrancar por primera vez (ya en el repo)
- Runner: `pip install --no-build-isolation` fallaba sin setuptools → ahora copia el paquete a `site-packages` (`python -I` ignora `PYTHONPATH`).
- Smokescreen no tiene imagen pública oficial → se compila desde Go (`deploy/egress-proxy/Dockerfile`, `golang:1`, versión `latest` solo en dev); `allow_missing_role: true` va en `deploy/egress-proxy/config.yaml` (`--config-file`), no en el ACL (corregido y verificado el 2026-10-18).
- Gunicorn: la opción es `--access-logformat` (no `--access-log-format`); se añadió `--no-control-socket`.
- Celery: `--app` apunta a módulos `nave.bootstrap.celery_worker` / `celery_beat` (una fábrica no vale).
- El runner exige imágenes de escáner por digest: `dev_up.ps1` usa el digest de lo recién construido.
- Worker: en dev, `egress-proxy` sin healthcheck y `depends_on: service_started` (override en `docker-compose.dev.yml`).

## 6. Pendiente y riesgos abiertos
1. **Ejecutar `dev_up.ps1` y `dev_demo.ps1`** con los últimos cambios (frontend rediseñado, `--no-control-socket`) y comprobar que la interfaz se ve bien con datos.
2. Escaneos reales: necesitan una **GitHub App real** (clave en `deploy/secrets/github_app_private_key`, `NAVE_GITHUB_APP_ID`, `NAVE_GITHUB_ALLOWED_ACCOUNTS`). Sin ella no hay organizaciones reales ni escaneos.
3. Comprobar con tráfico real que Smokescreen deja pasar solo `github.com`, `api.github.com`, `codeload.github.com` (supuesto no verificado: `allow_missing_role` en el ACL). Sin sonda de salud en dev (la imagen no trae `nc`).
4. Lista «Verificación pendiente» de `docs/architecture/despliegue.md` §6 (8 puntos) y riesgos RR-17 (`nginx -t` + cabeceras reales), RR-19 (nunca se había ejecutado Docker), AM-822 (los controles de triage/cancelar se muestran siempre; el servidor responde 403).
5. Smokescreen y Keycloak sin fijar versión/digest en dev; en producción hay que fijarlos (`pin_images.py`, `images.lock.env`).
6. Warning de la API al arrancar (`Read-only file system: '/.gunicorn'`) → debería desaparecer con `--no-control-socket`; confirmar.
7. Fase 7 (revisión de seguridad completa) una vez verificado el despliegue.

## 7. Cómo trabajar en este repo
**Backend** (desde `backend/`): venv con `uv`; `PYTHONPATH=src`; Postgres de pruebas con `pgserver`/`initdb`; `ruff`, `mypy`, `lint-imports`, `pytest`. Variables de configuración: `on`/`off` (no `true`), secretos solo como `NAVE_*_FILE`, una variable `NAVE_*` desconocida es un error, y cada servicio rechaza las variables del otro (`API_ONLY_VARIABLES`/`WORKER_ONLY_VARIABLES`).

**Frontend** (desde `frontend/`): en Windows `npm ci` y luego `npm run check` (lint + tipos + vitest, 233 tests) y `npm run build`. No instales `node_modules` de Linux en la carpeta del proyecto.

**Despliegue**: `deploy/tests` (46 tests estáticos con pytest + PyYAML, `deploy/requirements-test.lock`). Compose exige `${NAVE_*_IMAGE:?}`; en producción todas las imágenes por digest (ADR 0022). Redes `edge`/`internal`/`runner`/`egress` son `internal: true`; solo `frontend` publica puertos.

## 8. Cosas que NO hay que deshacer
- CSP estricta (sin estilos ni scripts en línea, sin fuentes externas), sin `v-html`.
- Severidad siempre con etiqueta de texto, no solo color.
- Sin `box-shadow`/radios fuera de tokens; colores solo en `frontend/src/app/styles/tokens.css` (lo comprueban `tests/style`).
- Secretos solo por Docker secrets; nunca en variables de entorno ni en el repo.
- Cambiar tokens, capas o decisiones estructurales exige ADR nuevo (los aceptados no se editan, se sustituyen).

## 9. Primer encargo sugerido para Claude Code
1. Ejecutar `dev_up.ps1` y `dev_demo.ps1`; si algo falla, `dev_logs.ps1` y leer `deploy/dev-logs.txt`.
2. Verificar de extremo a extremo con navegador (login, home con `demo-corp`, repositorio, escaneo, hallazgos, triage).
3. Cerrar los puntos 3 y 4 de §6 y actualizar `docs/threat-model.md` (RR-17, RR-19, AM-822).
4. Después, planificar la fase 7.
