# 0007 — Egress controlado hacia GitHub

- **Estado:** Aceptado (2026-10-11, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §15

## Contexto

§15 exige "egress limitado del worker solo a GitHub". Con ADR 0002, el contenedor de fetch también necesita salir a GitHub. Docker no filtra la salida por dominio: una red no interna permite salir a cualquier sitio.

## Opciones consideradas

1. **Proxy de salida con allowlist de dominios** (p. ej. Smokescreen, diseñado para esto: bloquea IPs privadas y de metadatos y permite solo hosts listados).
   - A favor: portable, también funciona en Docker Desktop, auditable.
   - En contra: es otro servicio.
2. **Firewall del host** (cadena `DOCKER-USER` con los rangos IP de `api.github.com/meta`).
   - A favor: no añade servicios.
   - En contra: los rangos cambian (hay que mantenerlos al día), no es portable a Docker Desktop y queda fuera de Compose.
3. **Sin restricción.** Incumple §15. Descartada.

## Decisión

Opción 1: un servicio `egress-proxy` en una red `egress` compartida solo con `worker` y los contenedores de fetch. Allowlist: `github.com`, `api.github.com`, `codeload.github.com`, y el mirror de la BD de Trivy para el job de actualización. Los clientes usan `HTTPS_PROXY`. Los contenedores no tienen otra ruta de salida.

## Superficie de ataque y mitigaciones

AM-507. El proxy no descifra TLS y filtra por SNI/CONNECT. Un repo hostil no puede exfiltrar a dominios propios; sí podría a un repo de GitHub, lo que exigiría un token con permiso de escritura que el contenedor de fetch no tiene.

## Consecuencias

Añade el servicio de infraestructura `egress-proxy`. §15 y §21 de CLAUDE.md se actualizan en el mismo cambio.
