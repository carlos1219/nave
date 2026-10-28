# 0024 — Ficheros que Semgrep omite por tamaño: visibles y salida incompleta

- **Estado:** Aceptado (2026-10-18; fase 7, hallazgo F7-02; decisión del responsable del proyecto: opción «reportar los omitidos»)
- **Fecha:** 2026-10-18
- **Sección de CLAUDE.md afectada:** §8 (ejecución de escáneres), §7 (quality gates)
- **Desarrolla:** ADR 0002 (runner) y ADR 0003 (políticas)

## Contexto

Semgrep descarta sin aviso los ficheros mayores que `--max-target-bytes` (2 MB en el envoltorio `nave-scan`). Verificado con Semgrep 1.180.0 y el sandbox real: un `.py` de 2,4 MB con `eval(input())` produce 0 resultados, ninguna notificación y `executionSuccessful: true`. Quien controla el repositorio puede ocultar código al quality gate rellenando un fichero (RR-22).

Del contenedor de escaneo solo llega al worker la salida estándar (el SARIF); la salida de error no se transmite. Por eso el aviso tiene que viajar dentro del SARIF.

## Decisión

1. `nave-scan` (Semgrep) ya no hace `exec` de Semgrep: escribe el SARIF en `/tmp` (tmpfs del escaneo) y ejecuta `nave_oversized.py`, que recorre `repo/` sin seguir enlaces y añade un resultado de nivel `warning` por cada fichero regular mayor que el límite.
   - Regla de plataforma `nave.scan.oversized-file-skipped`, una por fichero (ruta `repo/...` codificada), hasta 100.
   - Si hay más de 100, un único resultado sin ubicación `nave.scan.oversized-files-more`, con mensaje constante para que su huella sea estable.
   - El límite vive en una sola variable del envoltorio (`MAX_TARGET_BYTES`) que usan Semgrep y el ayudante.
2. El adaptador del worker (`RunnerScanner`) recibe un predicado opcional `incomplete_when`. Para Semgrep, cualquier resultado cuyo `ruleId` empiece por `nave.scan.oversized-` marca la salida como incompleta (`truncated`).
3. Consecuencias ya existentes de «incompleto» (sin cambios en `application`): línea `WARNING` en el registro del escaneo con `truncated=true`, veredicto `NEUTRAL` en lugar de `PASS` (regla `results-truncated`), un `FAIL` se mantiene, no se cierra automáticamente ningún finding como corregido y el resumen muestra `truncated`.
4. Cada fichero omitido es además un finding `MEDIUM` visible en la UI, con triage y trazabilidad como cualquier otro.
5. Fallo cerrado: si el SARIF de Semgrep no se puede leer o completar, el ayudante sale con código 70 y el runner informa `scanner_failed`; nunca se entrega un SARIF sin completar.

## Alternativas descartadas

- **Fallar el escaneo si hay algún fichero grande:** el más estricto, pero rompe repositorios legítimos con ficheros generados o vendorizados; se puede añadir más adelante como política configurable.
- **Subir el límite:** reduce la evasión sin eliminarla y aumenta CPU y memoria por escaneo.

## Superficie de ataque y mitigaciones

- El ayudante lee solo metadatos (`lstat`), no abre ficheros del repositorio y no sigue enlaces; las rutas se codifican como URI y el parser del worker las valida igual que el resto (AM-602).
- Un repositorio no puede fabricar estos resultados: el SARIF de Semgrep lo produce el binario con reglas de la plataforma y el ayudante añade los suyos después.
- Un escáner comprometido que emitiera estas reglas solo conseguiría degradar un `PASS` a `NEUTRAL`.
- DoS: recorrido acotado por el tamaño del árbol, ya limitado por el fetcher (`max-files`, `max-bytes`); como mucho 101 resultados añadidos.

## Verificación

- `deploy/tests/test_semgrep_oversized.py`: umbral exacto, enlaces no seguidos, codificación de rutas, tope y resumen, SARIF inesperado y argumentos inválidos fallan, límite compartido con el envoltorio.
- `backend/tests/unit/infrastructure/test_scanner_adapters.py`: los resultados marcan `truncated`, el normalizador conserva los findings y los marca incompletos, una ejecución limpia no se marca.
- Con la imagen real de Semgrep y el sandbox (sin red, rootfs de solo lectura, sin capacidades): dos ficheros grandes, uno con espacios y `ñ`, aparecen como resultados junto al hallazgo normal.

## Límites conocidos

- Trivy tiene sus propios límites de tamaño para secretos; no se han revisado todavía (componente 1 de la fase 7, pendiente de contrato de Trivy).
- El umbral sigue siendo fijo (2 MB); hacerlo configurable por organización requeriría un ADR nuevo.
