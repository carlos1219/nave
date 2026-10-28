"""`CliEngine` contra un `docker` falso: un script que se comporta según el último
argumento del comando. Verifica el manejo real de procesos (plazos, tope de salida,
entrada estándar, entorno mínimo, limpieza), no los flags (eso es test_sandbox_spec)."""

import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nave_runner.engine import CliEngine, EngineError, Orphan, _parse_time
from nave_runner.templates import ContainerSpec, Mount

FAKE = """#!{python}
import os, sys, time
args = sys.argv[1:]
log = os.environ.get("FAKE_LOG")
with open(os.path.join(os.path.dirname(__file__), "calls.log"), "a") as f:
    f.write(" ".join(args) + "\\n")
here = os.path.dirname(__file__)
if args[0] == "run" and "--detach" in args:
    open(os.path.join(here, "holder-" + args[args.index("--name") + 1]), "w").close()
elif args[0] == "run":
    mode = args[-1]
    if mode == "mode:echo-stdin":
        sys.stdout.write(sys.stdin.read())
    elif mode == "mode:sleep":
        time.sleep(30)
    elif mode == "mode:flood":
        sys.stdout.write("x" * 1000000)
        sys.stdout.flush()
        time.sleep(30)
    elif mode == "mode:exit3":
        sys.exit(3)
    elif mode == "mode:env":
        sys.stdout.write(",".join(sorted(os.environ)))
    elif mode == "mode:no-read":
        sys.exit(0)
    elif mode == "mode:out":
        sys.stdout.write("hello")
elif args[:2] == ["volume", "inspect"] and "--format" not in args:
    here = os.path.dirname(__file__)
    sys.exit(0 if os.path.exists(os.path.join(here, "volume-" + args[-1])) else 1)
elif args[:2] == ["volume", "create"]:
    open(os.path.join(os.path.dirname(__file__), "volume-" + args[-1]), "w").close()
elif args[:2] == ["volume", "rm"]:
    p = os.path.join(os.path.dirname(__file__), "volume-" + args[-1])
    if os.path.exists(p):
        os.remove(p)
elif args[0] == "inspect" and "{{{{.State.Running}}}}" in args:
    p = os.path.join(here, "holder-" + args[-1])
    if os.path.exists(p):
        print("true")
    else:
        sys.exit(1)
elif args[0] == "ps":
    print("nave-fetch-" + "a" * 32)
    print("nave-hold-" + "c" * 32)
    print("something-else")
elif args[:2] == ["volume", "ls"]:
    print("nave-ws-" + "b" * 32)
elif args[0] == "inspect" or args[:2] == ["volume", "inspect"]:
    print("2026-10-12T10:00:00.123456789Z")
elif args[0] == "version":
    print("27.0.0")
elif args[0] == "rm":
    p = os.path.join(here, "holder-" + args[-1])
    if os.path.exists(p):
        os.remove(p)
elif args[0] == "kill":
    pass
"""


@pytest.fixture
def docker(tmp_path: Path) -> Path:
    path = tmp_path / "docker"
    path.write_text(FAKE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def spec(mode: str) -> ContainerSpec:
    return ContainerSpec(
        name="nave-scan-" + "a" * 32,
        image="img@sha256:" + "d" * 64,
        command=(mode,),
        network="none",
        workdir="/workspace",
        memory_bytes=1 << 30,
        cpus="1",
        mounts=(Mount("tmpfs", "/tmp", size_bytes=1000),),
        env=(),
        labels=(),
        runtime=None,
        seccomp_profile=None,
        stdin=True,
    )


def calls(docker: Path) -> list[str]:
    log = docker.parent / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def test_stdout_and_exit_code_are_returned(docker: Path) -> None:
    result = CliEngine(str(docker)).run(
        spec("mode:out"), stdin=None, timeout_seconds=10, max_output=100
    )
    assert (result.exit_code, result.stdout, result.timed_out, result.output_exceeded) == (
        0,
        b"hello",
        False,
        False,
    )


def test_nonzero_exit_code(docker: Path) -> None:
    result = CliEngine(str(docker)).run(
        spec("mode:exit3"), stdin=None, timeout_seconds=10, max_output=100
    )
    assert result.exit_code == 3


def test_stdin_reaches_the_container(docker: Path) -> None:
    result = CliEngine(str(docker)).run(
        spec("mode:echo-stdin"), stdin=b"secret-token\n", timeout_seconds=10, max_output=100
    )
    assert result.stdout == b"secret-token\n"
    assert not any("secret-token" in call for call in calls(docker))


def test_a_container_that_never_reads_stdin_does_not_break_the_runner(docker: Path) -> None:
    result = CliEngine(str(docker)).run(
        spec("mode:no-read"), stdin=b"x" * 10, timeout_seconds=10, max_output=100
    )
    assert result.exit_code == 0


def test_timeout_kills_and_cleans_up(docker: Path) -> None:
    result = CliEngine(str(docker)).run(
        spec("mode:sleep"), stdin=None, timeout_seconds=1, max_output=100
    )
    assert result.timed_out
    assert result.exit_code is None
    log = calls(docker)
    assert any(c.startswith("kill nave-scan-") for c in log)
    assert any(c.startswith("rm --force nave-scan-") for c in log)


def test_output_over_the_limit_is_discarded_and_flagged(docker: Path) -> None:
    result = CliEngine(str(docker)).run(
        spec("mode:flood"), stdin=None, timeout_seconds=20, max_output=1000
    )
    assert result.output_exceeded
    assert result.stdout == b""
    assert result.exit_code is None
    assert any(c.startswith("rm --force nave-scan-") for c in calls(docker))


def test_the_child_environment_is_minimal(docker: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVE_RUNNER_HMAC_KEY_FILE", "/run/secrets/key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak")
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    result = CliEngine(str(docker)).run(
        spec("mode:env"), stdin=None, timeout_seconds=10, max_output=1000
    )
    names = set(result.stdout.decode().split(","))
    assert "NAVE_RUNNER_HMAC_KEY_FILE" not in names
    assert "AWS_SECRET_ACCESS_KEY" not in names
    assert "DOCKER_HOST" in names


def test_a_missing_binary_is_an_engine_error(tmp_path: Path) -> None:
    engine = CliEngine(str(tmp_path / "missing"))
    with pytest.raises(EngineError):
        engine.run(spec("mode:out"), stdin=None, timeout_seconds=5, max_output=10)
    with pytest.raises(EngineError):
        engine.create_workspace("a" * 32, size_bytes=1000)
    assert not engine.ping()


def test_workspace_lifecycle(docker: Path) -> None:
    engine = CliEngine(str(docker))
    run_id = "c" * 32
    assert not engine.workspace_exists(run_id)
    engine.create_workspace(run_id, size_bytes=1 << 20)
    assert engine.workspace_exists(run_id)
    engine.remove_workspace(run_id)
    assert not engine.workspace_exists(run_id)
    engine.remove_workspace(run_id)
    assert any(c.startswith("rm --force nave-fetch-") for c in calls(docker))
    assert any(c.startswith("rm --force nave-scan-") for c in calls(docker))
    assert any(c.startswith("rm --force nave-hold-") for c in calls(docker))


def test_the_holder_is_started_detached_and_removed_with_the_workspace(docker: Path) -> None:
    engine = CliEngine(str(docker))
    run_id = "d" * 32
    holder = ContainerSpec(
        name=f"nave-hold-{run_id}",
        image="img@sha256:" + "d" * 64,
        command=("sleep", "86400"),
        network="none",
        workdir="/",
        memory_bytes=1 << 24,
        cpus="0.1",
        mounts=(Mount("volume", "/workspace", source=f"nave-ws-{run_id}"),),
        env=(),
        labels=(),
        runtime=None,
        seccomp_profile=None,
        detach=True,
    )
    engine.create_workspace(run_id, size_bytes=1 << 20)
    assert not engine.workspace_ready(run_id)  # volumen sin sostenedor: contenido perdido
    engine.start_holder(holder)
    assert any(c.startswith("run --detach --name nave-hold-") for c in calls(docker))
    assert engine.workspace_ready(run_id)
    engine.remove_workspace(run_id)
    assert not engine.workspace_ready(run_id)
    assert not engine.workspace_exists(run_id)


def test_a_holder_without_its_volume_is_not_a_ready_workspace(docker: Path) -> None:
    engine = CliEngine(str(docker))
    run_id = "e" * 32
    (docker.parent / f"holder-nave-hold-{run_id}").write_text("")
    assert not engine.workspace_ready(run_id)


def test_ping(docker: Path) -> None:
    assert CliEngine(str(docker)).ping()


def test_list_orphans_ignores_foreign_names_and_parses_nanosecond_timestamps(docker: Path) -> None:
    orphans = CliEngine(str(docker)).list_orphans()
    assert {(o.kind, o.name) for o in orphans} == {
        ("container", "nave-fetch-" + "a" * 32),
        ("container", "nave-hold-" + "c" * 32),
        ("volume", "nave-ws-" + "b" * 32),
    }
    assert all(
        o.created_at == datetime(2026, 10, 12, 10, 0, 0, 123456, tzinfo=UTC) for o in orphans
    )


def test_remove_orphan_refuses_unexpected_names(docker: Path) -> None:
    engine = CliEngine(str(docker))
    with pytest.raises(EngineError):
        engine.remove_orphan(Orphan("container", "my-database", datetime.now(UTC)))
    engine.remove_orphan(Orphan("volume", "nave-ws-" + "b" * 32, datetime.now(UTC)))
    assert any(c.startswith("volume rm --force nave-ws-") for c in calls(docker))


@pytest.mark.parametrize(
    ("raw", "expected_year"),
    [
        ("2026-10-12T10:00:00Z", 2026),
        ("2026-10-12T10:00:00.5+02:00", 2026),
        ("2026-10-12 10:00:00", 2026),
    ],
)
def test_time_parsing(raw: str, expected_year: int) -> None:
    parsed = _parse_time(raw)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.year == expected_year


@pytest.mark.parametrize("raw", ["", "garbage", "0000-00-00"])
def test_unparseable_times_are_none(raw: str) -> None:
    assert _parse_time(raw) is None


def test_environment_fixture_is_isolated() -> None:
    assert os.environ.get("FAKE_LOG") is None
