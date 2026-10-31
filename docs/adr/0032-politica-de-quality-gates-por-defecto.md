# 0032 — Política de quality gates por defecto para organizaciones nuevas

- **Estado:** Aceptado (2026-10-31; decisión delegada por el responsable del proyecto: «lo más seguro»)
- **Fecha:** 2026-10-31
- **Sección de CLAUDE.md afectada:** §7 (quality gates)
- **Relacionado:** ADR 0003 (motor de políticas), ADR 0030 (edición de la política)

## Contexto

En el primer escaneo real con GitHub (fase 7, C12) una PR con 2 hallazgos CRITICAL nuevos obtuvo el veredicto NEUTRAL («resultado informativo»): la organización acababa de registrarse y no tenía política, y una política vacía no garantiza nada. Toda organización nueva era, por tanto, permisiva hasta que un ADMIN publicara una política, lo contrario de un valor seguro por defecto.

## Decisión

- Regla de dominio `default_policy()`: una puerta `no-new-critical-or-high` de ámbito `NEW_FINDINGS` con máximo 0 para CRITICAL y 0 para HIGH. Sin plazos (SLA) ni fallo por plazo: no se puede saber qué plazos acepta cada equipo.
- `SyncInstallation` la publica como versión 1 al **dar de alta** una organización, en la misma transacción, y deja `policy.defaulted` en su auditoría.
- Una reinstalación (misma cuenta dentro del plazo de gracia) conserva la política que tuviera; no se sobrescribe.
- Las organizaciones ya existentes sin política no se migran: su ADMIN la publica desde la vista de gobierno (ADR 0030).

## Alternativas descartadas

- **Aplicar la política por defecto en el evaluador cuando no hay ninguna:** invisible en la interfaz y en el historial de versiones; un ADMIN no sabría qué se le aplica.
- **Política más estricta (ALL_OPEN o MEDIUM):** bloquearía repositorios con deuda previa desde el primer día; el objetivo es no empeorar, no exigir limpiar lo heredado.

## Consecuencias

- Una PR que introduce hallazgos CRITICAL o HIGH nuevos falla desde el primer escaneo; el ADMIN puede relajarlo publicando otra versión.
- Los hallazgos ya existentes, o los nuevos de severidad MEDIUM o inferior, no bloquean.
