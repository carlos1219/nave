# 0022 — Despliegue: Compose endurecido, Nginx, CI/CD y cadena de suministro

- **Estado:** Aceptado (2026-10-12; decisiones delegadas por el responsable del proyecto: «lo más seguro y que menos problemas dé»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §15, §20
- **Desarrolla:** `docs/architecture/despliegue.md`, ADR 0002, 0007, 0008, 0018, 0020

## Contexto

La fase 6 convierte el diseño de despliegue en artefactos ejecutables. Las restricciones vienen de §15: imágenes con digest, contenedores sin privilegios, redes segmentadas, un único puerto publicado, Nginx con CSP estricta, y CI que bloquea el merge, escanea la propia plataforma, genera SBOM y firma.

## Decisión

1. **Digests fuera del repositorio de código, obligatorios.** Todas las imágenes de Compose y los `ARG` de los Dockerfiles se resuelven desde `deploy/images.env` (plantilla: `deploy/images.env.example`). Compose usa `${VAR:?}`: sin valor, no arranca. Un test exige que cada valor de la plantilla y cada imagen de Compose sea una variable con `@sha256:`. Los digests no se pueden resolver sin acceso a los registros; `deploy/scripts/pin_images.py` los resuelve en la máquina del operador.
2. **Una imagen de backend para `api`, `worker`, `scheduler` y `migrate`**, multi-stage, dependencias con `--require-hashes`, sin herramientas de compilación en runtime, usuario 65532. Cambia solo el comando.
3. **Nginx sin privilegios** (`nginx-unprivileged`), `read_only`, TLS 1.2/1.3 con certificados como Docker secrets, estáticos de Vue compilados en la imagen. El registro de accesos escribe `$uri`, nunca `$request_uri`: el callback OIDC lleva `code` y `state` en la consulta. Las cabeceras de seguridad viven en un único fragmento incluido en cada `location` (en Nginx, un `add_header` local anula los heredados).
4. **Proxy de entrada de un salto.** Nginx sobrescribe `X-Forwarded-For` con `$remote_addr`; la API arranca con `NAVE_TRUSTED_PROXY_HOPS=1` (ADR 0018). Límite global por IP en `/api/` y en el webhook (ADR 0018).
5. **Redes.** `edge`, `internal`, `runner` y `egress` son `internal: true`; la única con salida a Internet es `public`, y solo la tienen `frontend` (para el puerto publicado) y `egress-proxy`. Así el proxy es la **única** ruta de salida de `worker`, `api` (discovery OIDC, ADR 0020) y contenedores de fetch: no depende de que el cliente respete `HTTPS_PROXY`. `egress` tiene nombre fijo `nave-egress` porque el runner conecta a ella los contenedores de fetch. Esto precisa lo escrito en §15 («`egress`: worker y fetch → proxy»). `postgres` y `redis` nunca publican puertos; solo `frontend` publica 80 y 443.
6. **Secretos** como Docker secrets en ficheros bajo `deploy/secrets/` (ignorado por git), generados por `deploy/scripts/init_secrets.py`. Cada servicio recibe solo los suyos (tabla de `despliegue.md`).
7. **Redis** con una entrada propia que escribe la configuración en `tmpfs` (la contraseña no aparece en los argumentos del proceso), usuario único con hash, sin comandos de administración ni persistencia.
8. **PostgreSQL** crea los roles `nave_app`, `nave_migrator` y `nave_maintenance` en el primer arranque, con contraseñas de Docker secrets. Es el único servicio con `cap_add` (lo exige el entrypoint oficial para ajustar propietarios); el resto lleva `cap_drop: [ALL]` sin excepciones.
9. **Keycloak** solo en el perfil `dev`, con reino importado y secreto de desarrollo; está exento de `read_only` y no es un límite de seguridad (ADR 0004).
10. **CI**: acciones fijadas por SHA de commit; trabajos de backend (ruff, mypy, import-linter, pytest con PostgreSQL), frontend (vue-tsc, ESLint, Vitest, deriva del contrato, build), runner y fetcher, validación estática de despliegue, autoescaneo con Semgrep y Trivy. **Release**: construcción de imágenes, SBOM CycloneDX con Syft y firma keyless con cosign sobre el digest.

## Superficie de ataque y mitigaciones

AM-920 a AM-929 en `docs/threat-model.md`.

## Consecuencias

- Un operador debe resolver los digests antes del primer arranque; sin ellos Compose se niega a arrancar (es el comportamiento deseado).
- El comportamiento real de Nginx, Compose, Smokescreen y Keycloak solo se puede verificar con Docker; ver «Verificación pendiente» en `docs/architecture/despliegue.md`.
