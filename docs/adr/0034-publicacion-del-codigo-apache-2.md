# 0034 — Publicación del código fuente bajo Apache-2.0

- **Estado:** Aceptado (2026-11-02; decisión del responsable del proyecto)
- **Fecha:** 2026-11-02
- **Sección de CLAUDE.md afectada:** §0 (ámbito)
- **Relacionado:** threat model AM-905 y RR-05

## Contexto

NAVE se diseñó como herramienta de uso interno. El responsable quiere publicar el repositorio en
GitHub como muestra de trabajo. CLAUDE.md pide revisar las licencias de reglas y escáneres antes de
cambiar el ámbito (AM-905).

## Revisión de licencias

| Componente | Licencia | ¿Se distribuye? | Compatible |
|---|---|---|---|
| Código de NAVE | Apache-2.0 | Sí | — |
| Reglas de Semgrep (`deploy/scanner-sandbox/semgrep/rules`) | Propias (4 reglas `nave.*`) | Sí | Sí |
| Reglas del registro de Semgrep | Semgrep Rules License | **No** se incluyen | — |
| Semgrep Community Edition | LGPL-2.1 | En la imagen de escáner, sin modificar | Sí |
| Trivy | Apache-2.0 | En la imagen de escáner | Sí |
| Smokescreen | MIT | Compilado en la imagen del proxy | Sí |
| gVisor | Apache-2.0 | No (runtime del host) | — |

## Decisión

- El código se publica bajo **Apache-2.0** (`LICENSE`, `NOTICE` con las licencias de terceros).
- NAVE sigue pensada para **desplegarse dentro de una organización**; publicar el código no la
  convierte en un servicio para terceros.
- Prohibido incorporar reglas del registro de Semgrep a este repositorio público. Quien despliegue
  NAVE puede montarlas en su instalación bajo su propia licencia.

## Consecuencias

- AM-905 deja de depender de «uso interno»: depende de no distribuir reglas del registro.
- Cualquier dependencia nueva en las imágenes debe tener una licencia compatible y figurar en `NOTICE`.
