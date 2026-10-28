# 0018 — Limitador de peticiones de la API: cubos de fichas en memoria por usuario y por IP de login

- **Estado:** Aceptado (2026-10-12; decisiones delegadas por el responsable del proyecto: «lo más seguro y que menos problemas dé»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §11, §12
- **Desarrolla:** ADR 0016 (sesiones y CSRF), ADR 0017 (API REST)

## Contexto

La fase 4 dejó abierto que un usuario con sesión válida, o un cliente anónimo, podía repetir peticiones sin freno: iniciar escaneos en bucle, recorrer listados o martillear el login. Hay que frenar el abuso sin bloquear a usuarios legítimos y sin añadir una dependencia ni una escritura en base de datos por petición.

## Opciones consideradas

1. **Contador en PostgreSQL.** Exacto entre procesos, pero añade una escritura a cada petición y convierte al limitador en un punto de contención y de fallo de la API.
2. **Redis.** Exacto y rápido, pero la API no lo usa hoy (solo el worker y el broker); sería una dependencia y un nuevo modo de fallo para esto.
3. **Cubos de fichas en memoria, por proceso (elegida).** Sin dependencias ni E/S. El límite efectivo es N veces el configurado con N procesos: sirve como defensa en profundidad, no como cuota. El límite duro por IP queda para el proxy inverso.

## Decisión

Opción 3, en `delivery/http/rate_limit.py`.

- **Por usuario autenticado** (clave: `user_id`, nunca la IP): lecturas 60 de ráfaga y 120/min; escrituras (métodos no seguros) 10 de ráfaga y 30/min, en cubo propio. Se consume tras autenticar y antes de la comprobación CSRF.
- **Por IP, solo `GET /auth/login` y `GET /auth/callback`:** 10 de ráfaga y 20/min.
- **Peticiones con cookie inválida: sin límite por IP.** Limitarlas dejaría a los usuarios legítimos que comparten dirección (NAT, o un proxy no declarado) a merced de cualquiera que enviara basura. El coste de rechazarlas es una consulta por clave primaria; el control de inundación pertenece al proxy.
- **Sin límite:** `/healthz`, `/readyz` (sondas del orquestador) y el webhook (firmado con HMAC y con cuerpo acotado; GitHub envía ráfagas legítimas).
- **IP del cliente:** `request.remote_addr`. Con `NAVE_TRUSTED_PROXY_HOPS` (0–3, por defecto 0, solo API) se aplica `ProxyFix(x_for=N)`, que toma la entrada de `X-Forwarded-For` que añadió el N-ésimo proxy desde la derecha. Con 0, la cabecera se ignora por completo y no se puede falsificar. Un valor mayor que el real permite falsificar la IP; uno menor agrupa a todos los clientes bajo la IP del proxy (solo afecta al login).
- **Respuesta:** 429 `application/problem+json` con `Retry-After` en segundos (redondeado hacia arriba, mínimo 1). Documentada en `contracts/openapi.json` para todas las operaciones.
- **Memoria acotada:** como máximo 10 000 cubos, con expulsión del menos usado recientemente. Un atacante que fabricara claves solo puede reiniciar cubos ajenos, nunca agotar la memoria.
- **Reloj:** el del sistema, inyectado; un reloj que retrocede no concede fichas.

## Superficie de ataque y mitigaciones

Ver AM-815. El limitador no es una frontera de autorización: su fallo o elusión no concede acceso, solo permite más peticiones. Por eso no depende de entradas del cliente salvo la IP y solo bajo configuración explícita.

## Consecuencias

- Nueva variable `NAVE_TRUSTED_PROXY_HOPS` (solo API; el worker rechaza arrancar con ella).
- Con varios procesos o réplicas, el límite real escala con su número. Si hace falta uno global, se sustituye la implementación detrás de la misma interfaz (`take`/`peek`) sin tocar las rutas.
- El despliegue (fase 6) debe fijar `NAVE_TRUSTED_PROXY_HOPS` al número real de proxies y aplicar límite por IP en el proxy.
