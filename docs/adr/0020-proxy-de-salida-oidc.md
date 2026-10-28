# 0020 — Proxy de salida para el cliente OIDC

- **Estado:** Aceptado (2026-10-12; decisiones delegadas por el responsable del proyecto: «lo más seguro y que menos problemas dé»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §12
- **Desarrolla:** ADR 0016 (login OIDC)

## Contexto

El cliente OIDC (ADR 0016) se conecta directamente al emisor. En despliegues con salida restringida a un proxy, el login no podía funcionar. El worker ya sale a GitHub por `NAVE_EGRESS_PROXY_URL`, pero esa variable es solo del worker: la API rechaza arrancar si la encuentra, para que un contenedor expuesto no herede configuración ajena.

## Decisión

- Variable propia `NAVE_OIDC_PROXY_URL`, solo API (`API_ONLY_VARIABLES`; el worker la rechaza). Forma `http://host:puerto`, sin credenciales, ruta, consulta ni fragmento; HTTPS al proxy no se admite (igual que el proxy de GitHub).
- Túnel `CONNECT` (`set_tunnel`): TCP al proxy y TLS de extremo a extremo con el emisor, con verificación del nombre del emisor. El proxy filtra por host de destino, pero no ve ni puede alterar el contenido, incluido el secreto del cliente.
- Solo con emisor HTTPS. El perfil de desarrollo (HTTP a bucle local) no admite proxy.
- Todo lo demás del ADR 0016 se mantiene: mismo origen exigido a todos los endpoints, sin redirecciones, respuestas y tiempos acotados. El proxy no ensancha los orígenes permitidos: la comprobación ocurre antes de elegir la conexión.
- No se leen `HTTPS_PROXY` ni `NO_PROXY` del entorno: la ruta de salida es siempre explícita.
- Un valor no válido impide arrancar.

## Superficie de ataque y mitigaciones

Un proxy comprometido puede denegar el servicio del login, pero no suplantar al IdP (TLS de extremo a extremo y validación del ID token) ni leer el secreto del cliente. Ver AM-812.

## Consecuencias

- Una variable más, documentada en el despliegue de la fase 6.
- Los dos proxies de salida (GitHub en el worker, OIDC en la API) se configuran por separado y pueden apuntar al mismo.
