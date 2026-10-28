# 0004 — Cliente OIDC genérico y modelo de organizaciones

- **Estado:** Aceptado (2026-10-11, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §12, §23

## Contexto

§12 exige OIDC/SSO, RBAC (`viewer`, `triager`, `admin`) por organización y repositorio, y autorización en cada caso de uso.

## Opciones consideradas

1. **Cliente OIDC genérico basado en discovery** (`/.well-known/openid-configuration`): funciona con cualquier proveedor conforme (Keycloak, Entra ID, Google, Okta, Auth0).
2. **Login con GitHub.** El login de usuarios de GitHub es OAuth 2.0, no OIDC, así que no cumple §12. Además acopla la autorización de NAVE a los permisos de GitHub.
3. **Atar el código a un proveedor concreto.** Crea dependencia del proveedor sin ninguna ventaja.

## Decisión

- **Opción 1**, con Authorization Code + PKCE y cliente confidencial. Se validan `state`, `nonce`, firma por JWKS, `iss`, `aud`, `exp` e `iat`.
- Librería: **Authlib**. Justificación: madura, licencia BSD, mantenida, y evita escribir validación de JWT a mano (validarlos a mano es la fuente clásica de fallos).
- Desarrollo: **Keycloak** en un perfil de Compose `dev`, que no existe en producción.
- **Modelo de organizaciones:**
  - `Organization` 1:1 con una instalación de la GitHub App.
  - `Repository` pertenece a una `Organization`.
  - `User` se identifica por `(iss, sub)`, nunca por email.
  - `Membership(user, organization, role)` se gestiona en NAVE por los `admin` de esa organización.
  - El primer `admin` se crea con un comando CLI, no por API.
  - Mapeo opcional de un claim de grupos OIDC a rol, desactivado por defecto.
  - Excepciones de rol por repositorio: se aplazan a una iteración posterior.

## Superficie de ataque y mitigaciones

AM-101, AM-106 y AM-107. La sesión de NAVE es independiente del token del proveedor: el access token no se guarda en el navegador.

## Consecuencias

La aplicación necesita `MembershipRepository` y una regla de autorización pura en `domain`. Se añaden a §5.2 de CLAUDE.md (detalle en `docs/architecture/puertos-y-contratos.md`).
