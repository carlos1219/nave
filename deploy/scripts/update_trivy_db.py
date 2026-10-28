"""Actualiza el volumen `nave-trivy-db` (RR-15). Se ejecuta en el HOST, fuera de Compose.

    NAVE_TRIVY_IMAGE=ghcr.io/propietario/nave-trivy@sha256:... python deploy/scripts/update_trivy_db.py

Se programa con cron o un temporizador de systemd (p. ej. cada 12 h). Corre en la red por
defecto de Docker (con salida a Internet) y no en `nave-egress`: así la allowlist de
`egress-proxy` no incluye ningún registro de bases de datos. El contenedor no tiene
capacidades, tiene el sistema de ficheros de solo lectura y solo escribe en el volumen. El
escáner lo monta en solo lectura (ADR 0007).
"""

import os
import re
import subprocess
import sys

VOLUME = "nave-trivy-db"
IMAGE = re.compile(r"[a-z0-9][a-z0-9._/:-]{0,200}@sha256:[0-9a-f]{64}")
HARDENING = [
    "--rm",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
    "--memory",
    "1g",
    "--pids-limit",
    "128",
    "--tmpfs",
    "/tmp:size=256m",
]


def commands(image: str) -> list[list[str]]:
    """Dos pasos: dar el volumen al usuario sin privilegios y descargar la base de datos."""
    chown = ["docker", "run", *HARDENING, "--cap-add", "CHOWN", "--user", "0:0", "-v", f"{VOLUME}:/db"]
    chown += ["--entrypoint", "chown", image, "65532:65532", "/db"]
    # La descarga (cientos de MB) no cabe en el tmpfs de /tmp, que además cuenta contra el
    # límite de memoria: los temporales van al propio volumen, en disco.
    fetch = ["docker", "run", *HARDENING, "--user", "65532:65532", "-v", f"{VOLUME}:/db"]
    fetch += ["-e", "TMPDIR=/db", image]
    fetch += ["trivy", "image", "--download-db-only", "--cache-dir", "/db"]
    fetch += ["--no-progress", "--quiet", "--disable-telemetry"]
    return [["docker", "volume", "create", VOLUME], chown, fetch]


def main() -> int:
    image = os.environ.get("NAVE_TRIVY_IMAGE", "")
    if not IMAGE.fullmatch(image):
        print("NAVE_TRIVY_IMAGE debe ser una imagen fijada por digest", file=sys.stderr)
        return 2
    for command in commands(image):
        subprocess.run(command, check=True, timeout=1800)  # noqa: S603
    return 0


if __name__ == "__main__":
    sys.exit(main())
