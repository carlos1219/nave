# Reglas de Semgrep de la plataforma

Este directorio es lo único que el contenedor de escaneo usa como reglas (`--config /rules`).
Las reglas del repositorio escaneado nunca se cargan (AM-503).

Contenido: reglas revisadas y versionadas aquí. Para incorporar un conjunto del registro de
Semgrep, cópialo con su licencia y revisa que `AM-905` sigue siendo cierto (uso interno).
Cada cambio pasa por revisión y por `tests/contract/test_scanner_ignores_repo_config`.

`nave-baseline.yaml` es un conjunto mínimo de arranque, no un sustituto de un conjunto de
reglas completo.
