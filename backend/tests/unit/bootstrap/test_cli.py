"""CLI de operación: primer administrador y listado de usuarios (ADR 0016)."""

import io
from uuid import UUID

import pytest

from nave.application.use_cases.bootstrap_access import BootstrapAccess
from nave.bootstrap.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, run
from nave.domain.value_objects.identifiers import UserId
from nave.domain.value_objects.role import Role
from tests.fakes.auth import ALICE, make_auth
from tests.fakes.memory import FakeClock, Store
from tests.unit.application.builders import ORG_ID, seed_tenancy


class Cli:
    def __init__(self) -> None:
        self.store = Store()
        seed_tenancy(self.store)
        self.clock = FakeClock()
        kit = make_auth(self.store, self.clock)
        started = kit.begin.execute()
        kit.complete.execute(state=kit.oidc.urls[0]["state"], code="good", binding=started.binding)
        (self.user,) = self.store.state.users.values()
        self.access = BootstrapAccess(uow_factory=self.store.uow, clock=self.clock)

    def run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = run(argv, {}, out=out, err=err, access=self.access)
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def cli() -> Cli:
    return Cli()


def test_list_users_prints_one_tab_separated_line_per_user(cli: Cli) -> None:
    code, out, _ = cli.run("list-users")
    assert code == EXIT_OK
    fields = out.rstrip("\n").split("\t")
    assert fields[0] == str(cli.user.id)
    assert fields[2:] == ["Alice", "-", ALICE.issuer]


def test_grant_admin_makes_the_user_admin(cli: Cli) -> None:
    code, out, err = cli.run("grant-admin", "--organization", "acme", "--user-id", str(cli.user.id))
    assert (code, out, err) == (EXIT_OK, "ok\n", "")
    assert cli.store.state.memberships[(cli.user.id, ORG_ID)] is Role.ADMIN


def test_grant_admin_is_idempotent(cli: Cli) -> None:
    args = ("grant-admin", "--organization", "acme", "--user-id", str(cli.user.id))
    assert cli.run(*args)[0] == EXIT_OK
    assert cli.run(*args)[0] == EXIT_OK


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("grant-admin", "--organization", "nope", "--user-id", "{user}"), EXIT_FAILED),
        (
            ("grant-admin", "--organization", "acme", "--user-id", str(UserId(UUID(int=99)))),
            EXIT_FAILED,
        ),
        (("grant-admin", "--organization", "acme", "--user-id", "not-a-uuid"), EXIT_USAGE),
        (("grant-admin", "--organization", "acme"), EXIT_USAGE),
        (("explode",), EXIT_USAGE),
        ((), EXIT_USAGE),
        (("list-users", "--limit", "0"), EXIT_USAGE),
    ],
)
def test_failures_have_distinct_exit_codes_and_no_stack_traces(
    cli: Cli, args: tuple[str, ...], expected: int
) -> None:
    resolved = tuple(a.replace("{user}", str(cli.user.id)) for a in args)
    code, out, err = cli.run(*resolved)
    assert code == expected
    assert out == ""
    assert "Traceback" not in err


def test_missing_database_configuration_is_a_usage_error() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = run(["list-users"], {}, out=out, err=err)
    assert code == EXIT_USAGE
    assert "NAVE_DATABASE_URL" in err.getvalue()
    assert "password" not in err.getvalue().lower().replace("nave_database_password_file", "")


def test_platform_admin_is_granted_listed_and_revoked_with_audit(cli: Cli) -> None:
    user = str(cli.user.id)
    assert cli.run("grant-platform-admin", "--user-id", user) == (EXIT_OK, "ok\n", "")
    assert cli.run("grant-platform-admin", "--user-id", user) == (EXIT_OK, "unchanged\n", "")
    assert cli.run("list-platform-admins") == (EXIT_OK, f"{user}\n", "")
    assert cli.run("revoke-platform-admin", "--user-id", user) == (EXIT_OK, "ok\n", "")
    assert cli.store.state.platform_admins == {}
    actions = [e.action for e in cli.store.state.audit if e.target_id == user]
    assert actions[-2:] == ["platform_admin.granted", "platform_admin.revoked"]


def test_platform_admin_needs_a_known_user(cli: Cli) -> None:
    code, out, _ = cli.run("grant-platform-admin", "--user-id", str(UserId(UUID(int=99))))
    assert (code, out) == (EXIT_FAILED, "")
