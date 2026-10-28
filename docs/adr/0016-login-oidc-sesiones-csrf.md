# 0016 — Login OIDC, sesiones en PostgreSQL, CSRF y primer administrador por CLI

- **Estado:** Aceptado (2026-10-12; decisiones delegadas por el responsable del proyecto: «lo más seguro y que menos problemas dé»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §5.3, §11, §12, §23
- **Desarrolla:** ADR 0004 (identidad OIDC genérica), ADR 0015 (API HTTP)

## Contexto

ADR 0004 fijó OIDC genérico (Authorization Code + PKCE, cliente confidencial) y que la sesión es independiente del token del IdP. Faltan los detalles de implementación: dónde viven las sesiones, cómo se protege contra CSRF, cómo se valida el ID token y cómo nace el primer administrador.

## Opciones consideradas

1. **Sesión en cookie firmada sin estado (JWT o `itsdangerous`).** No se puede revocar ni expirar por inactividad sin estado en servidor.
2. **Sesión opaca en PostgreSQL** (token aleatorio de 256 bits, solo se guarda su SHA-256). Revocable, con caducidad absoluta e inactividad, sin infraestructura nueva. Un robo de la base de datos no da sesiones utilizables.
3. Sesión en Redis. Redis es caché/broker y puede perderse o vaciarse (CLAUDE.md §10); no es la fuente de verdad de la autenticación.
4. **Librería:** Authlib completo (ADR 0004) frente a `joserfc` (la capa JOSE que Authlib 1.8 usa por debajo) con HTTP propio de la biblioteca estándar. El flujo de código es corto y el cliente HTTP ya existe en el proyecto con el mismo patrón (sin redirecciones, respuesta acotada); solo hace falta una librería revisada para validar la firma JWT.

## Decisión

- **Dependencia nueva:** `joserfc==1.7.5` (validación de firma y claims del ID token). Se prohíben `none` y algoritmos simétricos: solo `RS256`, `PS256`, `ES256`, `EdDSA`.
- **Flujo:** `GET /api/v1/auth/login` genera `state`, `nonce` y verificador PKCE (S256), guarda su hash/valores en `login_flows` (válidos 10 min, **un solo uso**: se consumen con `DELETE … RETURNING`) y fija una cookie de enlace `__Host-nave_login` (HttpOnly, Secure, SameSite=Lax). El `callback` exige que el `state` exista, no haya caducado y que la cookie de enlace coincida con el flujo (anti *login CSRF* y *session fixation*); intercambia el código con `client_secret_basic`, valida el ID token (firma con el JWKS del emisor, `iss` exacto, `aud` = client id, `exp`, `iat`, `nonce`) y crea la sesión **nueva** (nunca reutiliza un identificador previo).
- **Descubrimiento:** `/.well-known/openid-configuration` del emisor configurado. El `issuer` del documento debe coincidir exactamente con el configurado y todos los endpoints deben ser HTTPS del **mismo origen** que el emisor. JWKS con caché acotada y un único refresco ante `kid` desconocido. Sin redirecciones, respuestas acotadas, tiempos máximos. HTTP solo para bucle local con `NAVE_DEV_INSECURE_HTTP=on` (perfil de desarrollo con Keycloak).
- **Identidad:** el usuario es `(iss, sub)`. Se crea al primer login (*JIT*) **sin ningún acceso**: la pertenencia a organizaciones la concede un administrador. El correo solo se guarda si `email_verified` es `true`, se muestra a los administradores y **nunca** se usa para autenticar ni para vincular cuentas.
- **Sesión:** cookie `__Host-nave_session` (Secure, HttpOnly, SameSite=Lax, Path=/, sin Domain), valor = 256 bits aleatorios; en base de datos solo `SHA-256`. Caducidad absoluta 12 h e inactividad 30 min (configurables dentro de límites). `last_seen_at` se actualiza como máximo cada 60 s. Cierre de sesión borra la fila. Limpieza oportunista y acotada de filas caducadas. Máximo de flujos de login vivos para que un anónimo no llene la tabla.
- **CSRF:** toda petición no segura (`POST/PUT/PATCH/DELETE`) autenticada por sesión exige (a) cabecera `Origin` idéntica al origen público configurado (`NAVE_PUBLIC_URL`; sin `Origin` se rechaza) y (b) cabecera `X-CSRF-Token` igual (`compare_digest`) al token propio de la sesión, que el cliente obtiene de `GET /api/v1/session`. El webhook no usa sesión (HMAC) y queda fuera. Un test recorre todas las rutas y falla si alguna no declara su modo de autenticación (denegar por defecto).
- **Primer administrador:** `python -m nave.bootstrap.cli grant-admin --organization <login> --user-id <uuid>` (y `list-users` para localizarlo tras su primer login). Es el único camino con `granted_by = NULL`; queda auditado. No hay rutas HTTP ni variables de entorno que den administración.
- **Roles por grupos del IdP:** no se implementa (decisión de ADR 0004); los roles solo los concede un administrador.
- **Configuración (solo API):** `NAVE_PUBLIC_URL`, `NAVE_OIDC_ISSUER`, `NAVE_OIDC_CLIENT_ID`, `NAVE_OIDC_CLIENT_SECRET_FILE`, `NAVE_SESSION_ABSOLUTE_HOURS`, `NAVE_SESSION_IDLE_MINUTES`, `NAVE_DEV_INSECURE_HTTP`. Están en `API_ONLY_VARIABLES`: el worker rechaza arrancar con ellas.

## Superficie de ataque y mitigaciones

AM-101 (robo/fijación de sesión), AM-102 (CSRF), AM-103 (suplantación de IdP: `iss`, `aud`, `nonce`, firma, mismo origen), AM-104 (errores genéricos), AM-106 (aislamiento por organización en cada caso de uso). Tests de seguridad: `test_session.py`, `test_csrf.py`, `test_oidc_validation.py`, `test_route_auth_declared.py`.

## Consecuencias

- Una migración (0005) añade `sessions` y `login_flows` y columnas de perfil en `users`; `nave_app` recibe `UPDATE` en `users` y CRUD en las dos tablas nuevas, nada más.
- El IdP caído impide *nuevos* logins, no las sesiones existentes.
- Riesgo residual: un XSS en el frontend podría usar la sesión (la cookie es HttpOnly, el token CSRF no); lo cubre la CSP del frontend (§13).
