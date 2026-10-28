# 0019 — Minimización de datos: vaciar nombre y correo de usuarios sin acceso

- **Estado:** Aceptado (2026-10-12; decisiones delegadas por el responsable del proyecto: «lo más seguro y que menos problemas dé»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §12
- **Desarrolla:** ADR 0006 (retención), ADR 0014 (purga de organizaciones), ADR 0016 (usuarios y sesiones)

## Contexto

Al purgar una organización desinstalada (RR-11) se borran sus membresías, pero los usuarios quedaban con nombre y correo para siempre, aunque ya no pertenezcan a ninguna organización. Esos datos personales dejan de tener finalidad. No se puede borrar la fila: `audit_log` (append-only, sin reescritura) y `triage_decisions` la referencian.

## Opciones consideradas

1. **Borrar el usuario.** Rompe referencias de auditoría y triage.
2. **Dejarlo como está** y documentarlo como riesgo. Conserva datos personales sin finalidad.
3. **Vaciar `display_name` y `email`, conservando la fila (elegida).** La identidad (`issuer`, `subject`) es un pseudónimo opaco del IdP y es lo que mantiene válidas las referencias.

## Decisión

Opción 3, como función SQL `nave_scrub_orphan_users(p_before, p_batch)` (migración 0006) y tarea diaria `scrub-orphaned-users` (03:55) del scheduler.

- Se vacían solo usuarios sin ninguna membresía, sin sesión no caducada y con último login anterior al plazo. La función impone un mínimo de 30 días sin login aunque el llamante pida menos.
- `nave_maintenance` recibe solo `EXECUTE` sobre la función (`SECURITY DEFINER`, `search_path` fijado, sin `PUBLIC`); sigue sin poder actualizar `users`. `nave_app` no puede ejecutarla.
- Lotes de 1 000 y tope de 50 lotes por ejecución.
- La medida es reversible por construcción: el siguiente login recompone nombre y correo desde el IdP.
- No se escribe en el audit log (no hay organización asociada ni acción de una persona); se registra el recuento en el log de la aplicación.

## Superficie de ataque y mitigaciones

Ver AM-816. Un worker comprometido solo puede vaciar datos personales de quien ya no tiene acceso; no puede borrar, leer sesiones ni tocar a usuarios con acceso o sesión viva.

## Consecuencias

- Tres entradas nuevas en las listas de tareas (nombre, planificación y registro) y una migración.
- `/session` y las listas de miembros muestran un nombre vacío para quien aún no ha vuelto a entrar tras ser vaciado, lo que ocurre solo si no tiene acceso a ninguna organización.
