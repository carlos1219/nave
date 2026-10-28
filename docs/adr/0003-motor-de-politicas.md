# 0003 — Motor de políticas propio detrás de `PolicyEvaluatorPort`

- **Estado:** Aceptado (2026-10-11, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-11
- **Sección de CLAUDE.md afectada:** §5.2, §7, §23

## Contexto

Los quality gates de §7 son umbrales por severidad, SLAs y excepciones con caducidad. Hay que decidir si se evalúan con lógica propia o con OPA (Rego).

## Opciones consideradas

1. **OPA.**
   - A favor: estándar, muy expresivo, políticas desacopladas del código.
   - En contra: es otro binario o servicio (choca con §21), obliga a aprender Rego, añade superficie de ataque y su expresividad no hace falta para umbrales.
2. **Motor propio declarativo.**
   - A favor: lógica pura en `domain`, testable sin I/O, sin dependencias nuevas.
   - En contra: si algún día se necesitan políticas arbitrarias, habrá que migrar.

## Decisión

Opción 2. La política es un documento versionado por organización, validado con esquema estricto:

```yaml
version: 1
gates:
  - id: no-new-critical-high
    scope: new_findings        # new_findings | all_open
    max: { CRITICAL: 0, HIGH: 0 }
  - id: medium-budget
    scope: all_open
    max: { MEDIUM: 20 }
sla_days: { CRITICAL: 7, HIGH: 30, MEDIUM: 90 }
fail_on_sla_breach: true
```

- El estado efectivo de triage se calcula en `domain`: un `ACCEPTED_RISK` caducado vuelve a contar como `OPEN`.
- El veredicto es `PASS | FAIL | NEUTRAL` con una lista de motivos. Se traduce a la conclusión del Check de GitHub (`success | failure | neutral`).
- El adaptador `infrastructure/policy/` solo carga y valida el documento. La evaluación son funciones puras de `domain/policies.py`.

## Superficie de ataque y mitigaciones

El documento de política solo lo modifica un `admin` (auditado). Se valida con esquema estricto y límite de tamaño, y no permite expresiones ni código.

## Consecuencias

Si en el futuro hace falta OPA, se escribe otro adaptador de `PolicyEvaluatorPort` sin tocar los casos de uso.
