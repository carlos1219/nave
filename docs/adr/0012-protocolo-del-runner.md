# 0012 — Protocolo del `scan-runner`, entrega del token y disposición del workspace

- **Estado:** Aceptado (2026-10-12, criterio delegado por el responsable del proyecto: "lo más seguro y que menos problemas dé")
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §6, §8, §15
- **Desarrolla / enmienda:** ADR 0002 (el token del clonado viaja por stdin, no por fichero tmpfs)

## Contexto

ADR 0002 decidió que existe un `scan-runner` y que es el único componente con acceso al motor de contenedores, pero no fijó el protocolo concreto ni cómo se entrega el token de instalación al contenedor de fetch. La fase 2 lo implementa.

## Decisión

**Protocolo v1.** Un único endpoint `POST /v1/runs`, cuerpo JSON canónico (claves ordenadas, compacto, ASCII, máx. 4096 bytes). Cabeceras `X-Nave-Timestamp`, `X-Nave-Nonce` (32 hex) y `X-Nave-Signature` = HMAC-SHA256 de
`NAVE-RUNNER-V1\nPOST\n/v1/runs\n<ts>\n<nonce>\n<sha256(cuerpo) hex>`.
Desfase máximo ±60 s; nonce de un solo uso; el runner rechaza además marcas anteriores a su propio arranque (un reinicio vacía el almacén de nonces y, sin esto, se podrían reproducir peticiones aún dentro de la ventana). El contrato compartido está en `contracts/runner-protocol-vectors.json` y lo verifican ambas implementaciones.

Pasos: `fetch` (`run_id`, `owner`, `name`, `commit_sha`, `token`, límites), `scan` (`run_id`, `scanner_kind`, límites) y `release` (`run_id`). Errores: 429 `busy` (Retry-After 15), 400, 401, 404, 413, 422, 503, 504, 500. Un `run_id` solo admite un paso a la vez (single-flight).

**El worker no elige nada ejecutable.** Imágenes (por digest), plantillas de contenedor y flags salen de la configuración del runner. Escaneo: `--network none`, workspace de solo lectura, cwd `/workspace`, objetivo `repo`, comando fijo `/usr/local/bin/nave-scan`.

**Token por stdin (enmienda a ADR 0002).** El token llega al contenedor de fetch por stdin y de ahí a git mediante `GIT_CONFIG_*` dentro del proceso; nunca en argumentos, en el entorno del contenedor ni en un fichero. Un fichero tmpfs dejaba el secreto en disco (memoria) accesible al resto del contenedor mientras vivía; stdin se consume y se cierra.

**Workspace.** Volumen tmpfs con `size`, `noexec`, uid 65532, modo 0700. El handle es el `run_id`. El path guard vive junto al fetcher (`deploy/scanner-sandbox/fetcher/pathguard.py`), porque es quien toca el sistema de ficheros del repo no confiable.

**Política de escáneres.**
- Semgrep con `--disable-nosem`; las `suppressions` de la salida se ignoran.
- Las reglas SAST de credenciales (CWE-798/259/321/522/532 o nombre de regla sensible) pierden snippet y mensaje antes de persistirse.
- Trivy usa un volumen de solo lectura `nave-trivy-db`; su sincronización queda diferida a la fase 6.

## Consecuencias

- Runner sin dependencias de terceros (solo stdlib), hablando con el motor por la CLI de `docker`.
- Los flags de Trivy no se han podido verificar contra el binario real (RR-12).
- HTTP sin TLS entre worker y runner dentro de la red `runner` (RR-13).
