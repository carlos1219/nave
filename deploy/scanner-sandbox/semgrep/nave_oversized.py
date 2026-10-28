"""Añade al SARIF de Semgrep un resultado por cada fichero que Semgrep omite por tamaño.

Semgrep descarta sin aviso los ficheros mayores que `--max-target-bytes` (resultados: 0,
sin notificaciones, `executionSuccessful: true`), de modo que quien controla el repositorio
puede ocultar código al quality gate rellenando un fichero (F7-02, RR-22). Este ayudante lo
vuelve visible: cada fichero omitido pasa a ser un resultado de la regla de plataforma
`nave.scan.oversized-file-skipped`, y el adaptador del worker marca la salida como incompleta.

Uso: nave_oversized.py <sarif de entrada> <directorio del repositorio> <máximo de bytes>.
Escribe el SARIF combinado en la salida estándar. Cualquier fallo sale con código distinto de
cero: un SARIF que no se pudo completar nunca se entrega como bueno.
"""

import json
import os
import stat
import sys
from pathlib import Path
from typing import Final
from urllib.parse import quote

RULE_FILE: Final = "nave.scan.oversized-file-skipped"
RULE_MORE: Final = "nave.scan.oversized-files-more"
MAX_LISTED: Final = 100
EXIT_FAILURE: Final = 70
_URI_PREFIX: Final = "repo/"


def oversized_files(root: Path, max_bytes: int) -> list[tuple[str, int]]:
    """Ficheros regulares de más de `max_bytes`, ordenados por ruta. No sigue enlaces."""
    found: list[tuple[str, int]] = []
    for directory, subdirs, names in os.walk(root, followlinks=False):
        subdirs.sort()
        for name in sorted(names):
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_size > max_bytes:
                found.append((path.relative_to(root).as_posix(), info.st_size))
    return sorted(found)


def _result(rule_id: str, message: str, relative_path: str | None) -> dict[str, object]:
    result: dict[str, object] = {
        "ruleId": rule_id,
        "level": "warning",
        "message": {"text": message},
    }
    if relative_path is not None:
        uri = _URI_PREFIX + quote(relative_path, safe="/", errors="replace")
        result["locations"] = [{"physicalLocation": {"artifactLocation": {"uri": uri}}}]
    return result


def synthetic_results(files: list[tuple[str, int]], max_bytes: int) -> list[dict[str, object]]:
    results = [
        _result(
            RULE_FILE,
            f"The file is larger than {max_bytes} bytes and was not analyzed by Semgrep.",
            path,
        )
        for path, _size in files[:MAX_LISTED]
    ]
    if len(files) > MAX_LISTED:
        results.append(
            _result(
                RULE_MORE,
                f"More than {MAX_LISTED} files larger than {max_bytes} bytes were not analyzed.",
                None,
            )
        )
    return results


def merge(document: object, extra: list[dict[str, object]]) -> dict[str, object]:
    if not isinstance(document, dict) or not isinstance(document.get("runs"), list):
        raise ValueError("unexpected SARIF structure")
    runs = document["runs"]
    if len(runs) != 1 or not isinstance(runs[0], dict):
        raise ValueError("unexpected SARIF runs")
    results = runs[0].setdefault("results", [])
    if not isinstance(results, list):
        raise ValueError("unexpected SARIF results")
    results.extend(extra)
    return document


def main(argv: list[str]) -> int:
    if len(argv) != 3 or not argv[2].isascii() or not argv[2].isdigit():  # noqa: PLR2004
        return EXIT_FAILURE
    sarif_path, repository, limit = Path(argv[0]), Path(argv[1]), int(argv[2])
    try:
        document = json.loads(sarif_path.read_bytes())
        merged = merge(document, synthetic_results(oversized_files(repository, limit), limit))
        sys.stdout.write(json.dumps(merged, separators=(",", ":")))
    except (OSError, ValueError, RecursionError):
        return EXIT_FAILURE
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
