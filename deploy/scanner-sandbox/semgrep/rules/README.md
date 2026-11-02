# Reglas de Semgrep de la plataforma

Este directorio es lo único que el contenedor de escaneo usa como reglas (`--config /rules`).
Las reglas del repositorio escaneado nunca se cargan (AM-503).

Contenido: reglas propias, revisadas y versionadas aquí. Este repositorio es público (ADR 0034):
**no** se añaden reglas del registro de Semgrep, cuya licencia no permite ofrecerlas como
servicio a terceros. Quien despliegue NAVE puede montarlas en su instalación (AM-905).
Cada cambio pasa por revisión y por `tests/contract/test_scanner_ignores_repo_config`.

`nave-baseline.yaml` es un conjunto mínimo de arranque, no un sustituto de un conjunto de
reglas completo.
