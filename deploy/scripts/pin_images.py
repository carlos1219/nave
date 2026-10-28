"""Resuelve las imágenes base de `deploy/images.env.example` a referencias con digest.

    python deploy/scripts/pin_images.py > deploy/images.lock.env

Requiere `docker buildx imagetools inspect` y acceso a los registros. Imprime solo las
variables de imágenes base; las propias (`NAVE_*_IMAGE` de ghcr.io) las rellena el workflow `release`.
"""

import re
import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "images.env.example"
BASE = {
    "NAVE_PYTHON_IMAGE",
    "NAVE_PYTHON_ALPINE_IMAGE",
    "NAVE_NODE_IMAGE",
    "NAVE_NGINX_IMAGE",
    "NAVE_POSTGRES_IMAGE",
    "NAVE_REDIS_IMAGE",
    "NAVE_GO_IMAGE",
    "NAVE_DOCKER_CLI_IMAGE",
    "NAVE_TRIVY_BASE_IMAGE",
    "NAVE_KEYCLOAK_IMAGE",
}
_REF = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,200}$")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def resolve(reference: str) -> str:
    name = reference.split("@", 1)[0]
    if not _REF.fullmatch(name):
        raise SystemExit(f"referencia no válida: {name!r}")
    out = subprocess.run(  # noqa: S603
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            name,
            "--format",
            "{{.Manifest.Digest}}",
        ],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout.strip()
    if not _DIGEST.fullmatch(out):
        raise SystemExit(f"digest inesperado para {name}")
    return f"{name}@{out}"


def main() -> int:
    print(
        "# Generado por deploy/scripts/pin_images.py. Se versiona; lo usa CI y el workflow release."
    )
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in BASE:
            print(f"{key}={resolve(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
