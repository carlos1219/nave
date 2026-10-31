"""Genera los secretos que se pueden generar, solo si faltan, en `deploy/secrets/`.

    python deploy/scripts/init_secrets.py [--gid 65532] [--dev]

No genera `github_app_private_key` (la descarga el administrador de la GitHub App),
`oidc_client_secret` (lo entrega el proveedor OIDC) ni el certificado TLS. Nunca sobrescribe.
En Linux, con `--gid`, deja los ficheros en 0440 de ese grupo (los contenedores corren como
65532); sin él, 0400 para el usuario actual.
"""

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1] / "secrets"
GENERATED = (
    "db_superuser_password",
    "db_app_password",
    "db_migrator_password",
    "db_maintenance_password",
    "redis_api_password",
    "redis_worker_password",
    "redis_scheduler_password",
    "webhook_secret",
    "runner_hmac_key",
    "fingerprint_key",
)
DEV_OIDC_SECRET = "dev-only-secret-not-for-production"  # noqa: S105 - el del reino de desarrollo
MANUAL = ("github_app_private_key", "oidc_client_secret", "tls_cert", "tls_key")


def write_secret(path: Path, gid: int | None) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    mode = 0o440 if gid is not None else 0o400
    descriptor = os.open(path, flags, mode)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(secrets.token_hex(32))
    if gid is not None and hasattr(os, "chown"):
        os.chown(path, -1, gid)
    if hasattr(os, "chmod"):
        os.chmod(path, stat.S_IMODE(mode))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gid", type=int, default=None)
    parser.add_argument(
        "--dev", action="store_true", help="escribe el secreto OIDC de Keycloak dev"
    )
    args = parser.parse_args()
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    for name in GENERATED:
        path = DIRECTORY / name
        if path.exists():
            print(f"existe, no se toca: {name}")
            continue
        write_secret(path, args.gid)
        print(f"creado: {name}")
    dev_secret = DIRECTORY / "oidc_client_secret"
    if args.dev and not dev_secret.exists():
        dev_secret.write_text(DEV_OIDC_SECRET, encoding="ascii")
        print(
            "creado: oidc_client_secret (valor de desarrollo, el del reino importado)"
        )
    for name in MANUAL:
        if not (DIRECTORY / name).exists():
            print(f"FALTA (manual): {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
