# 0031 — El administrador de plataforma no cambia su propia pertenencia

- **Estado:** Aceptado (2026-10-28; corrección de la revisión de seguridad de la fase 7, F7-31)
- **Fecha:** 2026-10-28
- **Sección de CLAUDE.md afectada:** §12 (administrador de plataforma)
- **Complementa:** ADR 0030

## Contexto

El ADR 0030 dice que el rol de administrador de plataforma «no da acceso a hallazgos ni escaneos de ninguna organización», y a la vez le permite cambiar el rol de cualquier usuario en cualquier organización. Combinadas, las dos reglas dejaban un atajo: `PUT /platform/users/{propio}/memberships/{org}` con `ADMIN` convertía el rol de plataforma en acceso completo a los datos de cualquier organización. Se reprodujo en vivo (204 y lectura del audit log de una organización ajena).

## Decisión

- `SetMembershipAsPlatform` rechaza cualquier cambio cuyo destinatario sea el propio actor (dar, cambiar o quitar rol): 403.
- El intento queda en la auditoría de la organización afectada como `access.denied` con `via=platform` y `reason=self`.
- El resto del ADR 0030 no cambia.

## Consecuencias

- Para obtener rol en una organización, un administrador de plataforma necesita que se lo dé un ADMIN de esa organización u otro administrador de plataforma, y queda auditado.
- Riesgo residual (RR-34): un administrador de plataforma puede dar un rol a otra cuenta que controle. Es la función pedida; queda `membership.changed` con `via=platform` en la organización afectada.
