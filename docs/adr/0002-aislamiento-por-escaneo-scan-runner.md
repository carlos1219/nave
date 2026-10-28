# 0002 — Ejecución aislada por escaneo mediante un servicio `scan-runner`

- **Estado:** Aceptado (2026-10-11, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §4, §8, §15, §21, §23
- **Nota (2026-10-12):** el protocolo concreto y la entrega del token (stdin, no fichero tmpfs) se detallan y enmiendan en el [ADR 0012](0012-protocolo-del-runner.md).

## Contexto

§8 exige un contenedor efímero por escaneo (sin red, rootfs de solo lectura, no root, `cap_drop ALL`, seccomp, límites) y prohíbe montar `docker.sock` en el worker o en el escáner. Si hace falta orquestar contenedores, §8 pide "un runner aislado con superficie mínima" documentado en un ADR. Este es ese ADR.

Fuerzas: el código escaneado lo controla el atacante (AT-2); el escáner puede tener vulnerabilidades (S-04); el worker tiene secretos críticos (clave de la GitHub App, credenciales de BD); el desarrollo se hace en Windows con Docker Desktop.

## Opciones consideradas

1. **Montar `docker.sock` en el worker.** Prohibido por §8: equivale a root en el host. Descartada.
2. **Proxy filtrado del socket** (tipo docker-socket-proxy). Filtra por endpoint, no por el cuerpo: un `POST /containers/create` con `Privileged: true` o un bind mount de `/` pasa el filtro. Equivale a root. Descartada.
3. **Sandbox dentro del worker** (nsjail o bubblewrap con user namespaces). Mantiene 5 servicios, pero:
   - obliga a un perfil seccomp del worker que permita `unshare`/`CLONE_NEWUSER`, lo que amplía la superficie del kernel justo en el contenedor con más secretos;
   - un escape del namespace compromete la clave de la GitHub App y la BD;
   - no hay imagen por escáner fijada por digest.
   Viable solo como alternativa de emergencia.
4. **Servicio `scan-runner` dedicado + motor de contenedores rootless + gVisor.** Recomendada.
5. **MicroVM** (Firecracker o Kata). Es el aislamiento más fuerte, pero requiere KVM (no disponible en Docker Desktop sobre Windows) y mucha más complejidad. Queda como evolución futura detrás del mismo contrato.

## Decisión

Opción 4:

- `scan-runner` es **el único componente que habla con un motor de contenedores**. No tiene credenciales de BD ni la clave de la GitHub App.
- Expone un único endpoint `POST /v1/runs` en la red `runner` (`internal: true`, solo `worker` y `scan-runner`). La autenticación es HMAC-SHA256 sobre el cuerpo, con marca temporal (±60 s) y nonce de un solo uso.
- Cuerpo cerrado: `{run_id, step: "fetch"|"scan", scanner_kind, owner, name, commit_sha}`. En el paso `fetch` va además el token de instalación limitado a ese repo, que el runner escribe en un tmpfs del contenedor de fetch. Todo se valida con regex y enums.
- La especificación del contenedor sale de **plantillas fijas del runner**, nunca de la petición:
  - imagen fijada por digest;
  - `--network none` para escanear; red de egress controlada para el fetch (ADR 0007);
  - `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, perfil seccomp, `--user 65532:65532`;
  - `--pids-limit`, `--memory`, `--cpus` y timeout de reloj;
  - `--runtime runsc` cuando esté disponible;
  - workspace en un volumen tmpfs con `size=` limitado, compartido entre fetch y escaneo, y ningún otro montaje.
- El runner devuelve la salida truncada a un tamaño máximo y borra contenedores y volumen en `finally`. Un barrido periódico elimina los huérfanos.
- **Producción:** socket del motor rootless (Docker rootless o Podman) de un usuario de host sin privilegios, con gVisor.
- **Desarrollo:** socket de Docker Desktop. No es un límite de seguridad (S-07).

Consecuencia arquitectónica: la aplicación **nunca ve rutas del sistema de ficheros** del código escaneado. Solo maneja un `WorkspaceHandle` opaco, y esto elimina la clase entera de path traversal en `application`.

## Superficie de ataque y mitigaciones

- Compromiso del runner → control del motor rootless con un usuario sin privilegios. Riesgo residual **medio** (RR-01). Mitigación: código mínimo, entrada de 6 campos, sin otras credenciales y revisión como atacante en fase 2.
- Falsificación de peticiones → red dedicada + HMAC + nonce.
- Ver AM-401 a AM-404 y AM-501 a AM-507 del threat model.

## Consecuencias

- Añade el servicio de infraestructura `scan-runner`. §4, §6 (directorio `runner/`), §8, §15 y §21 de CLAUDE.md se actualizan en el mismo cambio.
- `RepositoryFetcherPort` y `ScannerPort` se implementan en `infrastructure/sandbox/` como clientes del runner.
- El runner se implementa con dependencias mínimas en fase 2.
