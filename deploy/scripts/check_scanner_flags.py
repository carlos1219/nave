"""Comprueba que cada flag que usan los envoltorios existe en el binario real (RR-12).

    python deploy/scripts/check_scanner_flags.py <imagen> <envoltorio> <comando de ayuda...>
    python deploy/scripts/check_scanner_flags.py nave-trivy:ci deploy/scanner-sandbox/trivy/nave-scan trivy fs

Ejecuta `<comando> --help` dentro de la imagen y exige que aparezca cada `--flag` del
envoltorio. Un flag inexistente haría fallar todos los escaneos en producción.
"""

import re
import subprocess
import sys
from pathlib import Path

FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)")


def flags_in(script: str) -> set[str]:
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    return set(FLAG.findall(code))


def missing(flags: set[str], help_text: str) -> list[str]:
    return sorted(flag for flag in flags if flag not in help_text)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2
    image, wrapper, *command = argv
    wanted = flags_in(Path(wrapper).read_text(encoding="utf-8"))
    cmd = ["docker", "run", "--rm", "--entrypoint", "", image, *command, "--help"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)  # noqa: S603
    absent = missing(wanted, out.stdout + out.stderr)
    if absent:
        print(f"flags no reconocidos por `{' '.join(command)}`: {', '.join(absent)}", file=sys.stderr)
        return 1
    print(f"{len(wanted)} flags verificados para `{' '.join(command)}`")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
