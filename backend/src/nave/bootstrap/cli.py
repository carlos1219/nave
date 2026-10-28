"""Herramienta de operación: primer administrador y consulta de usuarios (ADR 0016).

    python -m nave.bootstrap.cli list-users
    python -m nave.bootstrap.cli grant-admin --organization acme --user-id <uuid>
    python -m nave.bootstrap.cli grant-platform-admin --user-id <uuid>     (ADR 0030)
    python -m nave.bootstrap.cli revoke-platform-admin --user-id <uuid>
    python -m nave.bootstrap.cli list-platform-admins

Es el único camino que concede un rol sin otro administrador previo: exige acceso al
entorno de operación (credenciales de base de datos), no hay ruta HTTP equivalente. Solo
necesita `NAVE_DATABASE_URL` y `NAVE_DATABASE_PASSWORD_FILE`.
"""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from nave.application.errors import ApplicationError, NotFoundError
from nave.application.use_cases.bootstrap_access import AmbiguousOrganizationError, BootstrapAccess
from nave.bootstrap.config import ConfigurationError, EnvReader, read_secret_file
from nave.domain.errors import DomainError
from nave.domain.value_objects.identifiers import UserId
from nave.infrastructure.persistence.engine import create_database_engine
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from nave.infrastructure.system import SystemClock

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nave-admin", description="Operación de NAVE")
    commands = parser.add_subparsers(dest="command", required=True)
    users = commands.add_parser("list-users", help="usuarios que han iniciado sesión")
    users.add_argument("--limit", type=int, default=50, choices=range(1, 501), metavar="1-500")
    grant = commands.add_parser("grant-admin", help="convierte a un usuario en ADMIN")
    grant.add_argument("--organization", required=True, help="login de la cuenta de GitHub")
    grant.add_argument("--user-id", required=True, help="UUID mostrado por list-users")
    commands.add_parser("list-platform-admins", help="administradores de plataforma (ADR 0030)")
    for name, text in (
        ("grant-platform-admin", "concede el rol de administrador de plataforma"),
        ("revoke-platform-admin", "retira el rol de administrador de plataforma"),
    ):
        platform = commands.add_parser(name, help=text)
        platform.add_argument("--user-id", required=True, help="UUID mostrado por list-users")
    return parser


def run(
    argv: Sequence[str],
    environ: Mapping[str, str],
    *,
    out: TextIO,
    err: TextIO,
    access: BootstrapAccess | None = None,
) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except SystemExit as exit_:
        return EXIT_USAGE if exit_.code else EXIT_OK
    try:
        service = access or _build(environ)
        _execute(arguments, service, out)
    except (ConfigurationError, DomainError) as error:
        err.write(f"error: {error}\n")
        return EXIT_USAGE
    except NotFoundError as error:
        err.write(f"error: {error}\n")
        return EXIT_FAILED
    except AmbiguousOrganizationError:
        err.write("error: more than one active installation uses that login\n")
        return EXIT_FAILED
    except ApplicationError as error:
        err.write(f"error: {error.code}\n")
        return EXIT_FAILED
    return EXIT_OK


def _execute(arguments: argparse.Namespace, service: BootstrapAccess, out: TextIO) -> None:
    if arguments.command == "list-users":
        for user in service.list_users(limit=arguments.limit):
            when = user.last_login_at.isoformat() if user.last_login_at else "-"
            fields = (str(user.id), when, user.display_name or "-", user.email or "-", user.issuer)
            out.write("\t".join(fields) + "\n")
        return
    if arguments.command == "list-platform-admins":
        for admin in service.list_platform_admins():
            out.write(f"{admin}\n")
        return
    if arguments.command in {"grant-platform-admin", "revoke-platform-admin"}:
        changed = service.set_platform_admin(
            user=UserId.parse(arguments.user_id),
            granted=arguments.command == "grant-platform-admin",
        )
        out.write("ok\n" if changed else "unchanged\n")
        return
    service.grant_admin(
        organization_login=arguments.organization, user=UserId.parse(arguments.user_id)
    )
    out.write("ok\n")


def _build(environ: Mapping[str, str]) -> BootstrapAccess:
    reader = EnvReader(environ, read_secret_file)
    url = reader.database_url("NAVE_DATABASE_URL", "NAVE_DATABASE_PASSWORD_FILE")
    return BootstrapAccess(
        uow_factory=SqlUnitOfWorkFactory(create_database_engine(url)), clock=SystemClock()
    )


def main() -> int:
    return run(sys.argv[1:], os.environ, out=sys.stdout, err=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
