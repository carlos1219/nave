"""AM-402, AM-501, AM-502, AM-503, AM-505, AM-506: propiedades de seguridad de las
plantillas de contenedor, comprobadas sobre los argumentos reales de `docker run`."""

import json

import pytest

from nave_runner.config import RunnerConfig
from nave_runner.schema import FetchRequest, ScanRequest, parse_request
from nave_runner.templates import (
    fetch_spec,
    hold_spec,
    render_run_arguments,
    render_volume_arguments,
    scan_spec,
    workspace_volume,
)
from tests.conftest import RUN, TOKEN, make_config

FORBIDDEN_FLAGS = {
    "--privileged",
    "--cap-add",
    "--pid",
    "--ipc",
    "--uts",
    "--userns",
    "--device",
    "--volume",
    "-v",
    "--volumes-from",
    "--security-opt=seccomp=unconfined",
    "--add-host",
    "--publish",
    "-p",
    "--network=host",
    "--sysctl",
}


def fetch_request(config: RunnerConfig, **overrides: object) -> FetchRequest:
    document = {
        "step": "fetch",
        "run_id": RUN,
        "owner": "acme",
        "name": "app",
        "commit_sha": "a" * 40,
        "token": TOKEN,
        "max_bytes": 1000,
        "max_files": 100,
        "timeout_seconds": 60,
    }
    document.update(overrides)
    request = parse_request(json.dumps(document).encode(), config)
    assert isinstance(request, FetchRequest)
    return request


def scan_request(config: RunnerConfig, kind: str = "semgrep") -> ScanRequest:
    document = {
        "step": "scan",
        "run_id": RUN,
        "scanner_kind": kind,
        "timeout_seconds": 600,
        "max_output_bytes": 1 << 20,
    }
    request = parse_request(json.dumps(document).encode(), config)
    assert isinstance(request, ScanRequest)
    return request


def pairs(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, value in enumerate(args) if value == flag]


@pytest.fixture(params=["fetch", "scan-semgrep", "scan-trivy"])
def args(request: pytest.FixtureRequest, config: RunnerConfig) -> list[str]:
    if request.param == "fetch":
        return render_run_arguments(fetch_spec(config, fetch_request(config)))
    kind = request.param.removeprefix("scan-")
    spec = scan_spec(config, scan_request(config, kind), config.scanners[kind])
    return render_run_arguments(spec)


class TestEveryContainer:
    def test_is_hardened(self, args: list[str]) -> None:
        assert "--read-only" in args
        assert pairs(args, "--cap-drop") == ["ALL"]
        assert "no-new-privileges:true" in pairs(args, "--security-opt")
        assert "seccomp=/etc/nave/seccomp.json" in pairs(args, "--security-opt")
        assert pairs(args, "--user") == ["65532:65532"]
        assert pairs(args, "--runtime") == ["runsc"]
        assert pairs(args, "--pull") == ["never"]
        assert pairs(args, "--log-driver") == ["none"]
        assert "--rm" in args

    def test_has_resource_limits(self, args: list[str]) -> None:
        assert int(pairs(args, "--pids-limit")[0]) <= 256
        memory = pairs(args, "--memory")
        assert memory == pairs(args, "--memory-swap")
        assert int(memory[0]) <= 2 << 30
        assert float(pairs(args, "--cpus")[0]) <= 2
        assert any(u.startswith("fsize=") for u in pairs(args, "--ulimit"))

    def test_has_no_dangerous_flags(self, args: list[str]) -> None:
        assert not FORBIDDEN_FLAGS & set(args)
        assert not any(a.startswith(("--privileged", "--cap-add", "--pid=")) for a in args)

    def test_mounts_are_only_named_volumes_and_tmpfs(self, args: list[str]) -> None:
        for mount in pairs(args, "--mount"):
            assert mount.startswith("type=volume,src=nave-")
            assert "bind" not in mount
            assert "/var/run" not in mount
            assert "docker.sock" not in " ".join(args)

    def test_tmpfs_is_never_executable_and_has_a_size(self, args: list[str]) -> None:
        tmpfs = pairs(args, "--tmpfs")
        assert tmpfs
        for entry in tmpfs:
            assert "noexec" in entry
            assert "nosuid" in entry
            assert "nodev" in entry
            assert "size=" in entry

    def test_is_labelled_for_the_orphan_sweep(self, args: list[str]) -> None:
        labels = pairs(args, "--label")
        assert "nave.managed=true" in labels
        assert f"nave.run={RUN}" in labels

    def test_image_is_pinned_by_digest(self, args: list[str]) -> None:
        images = [a for a in args if "@sha256:" in a]
        assert len(images) == 1


class TestHoldContainer:
    """ADR 0025: sostiene el volumen tmpfs montado entre el fetch y el escaneo."""

    def test_is_hardened_and_has_no_network(self, config: RunnerConfig) -> None:
        rendered = render_run_arguments(hold_spec(config, RUN))
        for flag in ("--detach", "--read-only", "--rm"):
            assert flag in rendered
        assert pairs(rendered, "--network") == ["none"]
        assert pairs(rendered, "--cap-drop") == ["ALL"]
        assert pairs(rendered, "--user") == ["65532:65532"]
        assert "no-new-privileges:true" in pairs(rendered, "--security-opt")
        assert pairs(rendered, "--runtime") == ["runsc"]
        assert int(pairs(rendered, "--pids-limit")[0]) <= 16
        assert int(pairs(rendered, "--memory")[0]) <= 64 << 20
        assert not FORBIDDEN_FLAGS & set(rendered)
        assert "--env" not in rendered
        assert "--tmpfs" not in rendered

    def test_mounts_only_the_run_workspace_and_runs_nothing_from_it(
        self, config: RunnerConfig
    ) -> None:
        rendered = render_run_arguments(hold_spec(config, RUN))
        assert pairs(rendered, "--mount") == [
            f"type=volume,src={workspace_volume(RUN)},dst=/workspace"
        ]
        assert rendered[-2] == "sleep"
        assert rendered[-1].isdigit()
        assert int(rendered[-1]) <= 86400

    def test_is_named_and_labelled_for_the_sweep_and_the_release(
        self, config: RunnerConfig
    ) -> None:
        rendered = render_run_arguments(hold_spec(config, RUN))
        assert pairs(rendered, "--name") == [f"nave-hold-{RUN}"]
        assert {"nave.managed=true", f"nave.run={RUN}", "nave.role=hold"} <= set(
            pairs(rendered, "--label")
        )

    def test_other_containers_are_not_detached(self, config: RunnerConfig) -> None:
        assert "--detach" not in render_run_arguments(fetch_spec(config, fetch_request(config)))


class TestFetchContainer:
    def test_token_never_appears_in_arguments_or_environment(self, config: RunnerConfig) -> None:
        spec = fetch_spec(config, fetch_request(config))
        rendered = render_run_arguments(spec)
        assert TOKEN not in " ".join(rendered)
        assert all(TOKEN not in value for _, value in spec.env)
        assert spec.stdin
        assert "--interactive" in rendered

    def test_only_the_fetch_container_has_network_and_only_through_the_proxy(
        self, config: RunnerConfig
    ) -> None:
        rendered = render_run_arguments(fetch_spec(config, fetch_request(config)))
        assert pairs(rendered, "--network") == ["nave-egress"]
        env = pairs(rendered, "--env")
        assert "HTTPS_PROXY=http://egress-proxy:3128" in env
        assert "GIT_TERMINAL_PROMPT=0" in env

    def test_the_workspace_is_the_only_writable_volume(self, config: RunnerConfig) -> None:
        rendered = render_run_arguments(fetch_spec(config, fetch_request(config)))
        assert pairs(rendered, "--mount") == [
            f"type=volume,src={workspace_volume(RUN)},dst=/workspace"
        ]

    @pytest.mark.parametrize("name", ["-x", "--x", "-", "a.b_c-d"])
    def test_request_values_cannot_become_options(self, config: RunnerConfig, name: str) -> None:
        spec = fetch_spec(config, fetch_request(config, name=name))
        command = spec.command[1:]
        assert all(part.startswith("--") and "=" in part for part in command)
        assert f"--name={name}" in command

    def test_command_is_the_fixed_binary_with_validated_values(self, config: RunnerConfig) -> None:
        spec = fetch_spec(config, fetch_request(config))
        assert spec.command[0] == "/usr/local/bin/nave-fetch"
        assert spec.command[1:] == (
            "--owner=acme",
            "--name=app",
            f"--sha={'a' * 40}",
            "--max-bytes=1000",
            "--max-files=100",
            "--timeout=60",
        )


class TestScanContainer:
    @pytest.mark.parametrize("kind", ["semgrep", "trivy"])
    def test_has_no_network_and_no_secrets(self, config: RunnerConfig, kind: str) -> None:
        spec = scan_spec(config, scan_request(config, kind), config.scanners[kind])
        rendered = render_run_arguments(spec)
        assert pairs(rendered, "--network") == ["none"]
        assert not spec.stdin
        assert "--interactive" not in rendered
        env_keys = {key for key, _ in spec.env}
        assert env_keys == {"HOME", "NAVE_SCAN_TIMEOUT_SECONDS"}
        assert not any("proxy" in key.lower() or "token" in key.lower() for key in env_keys)

    @pytest.mark.parametrize("kind", ["semgrep", "trivy"])
    def test_workspace_is_read_only(self, config: RunnerConfig, kind: str) -> None:
        rendered = render_run_arguments(
            scan_spec(config, scan_request(config, kind), config.scanners[kind])
        )
        workspace = [m for m in pairs(rendered, "--mount") if "dst=/workspace" in m]
        assert workspace == [f"type=volume,src={workspace_volume(RUN)},dst=/workspace,readonly"]
        assert not any("rw" in m.split(",") for m in pairs(rendered, "--mount"))

    @pytest.mark.parametrize("kind", ["semgrep", "trivy"])
    def test_runs_outside_the_repository_tree(self, config: RunnerConfig, kind: str) -> None:
        spec = scan_spec(config, scan_request(config, kind), config.scanners[kind])
        assert spec.workdir == "/workspace"
        assert not spec.workdir.endswith("/repo")

    @pytest.mark.parametrize("kind", ["semgrep", "trivy"])
    def test_the_command_is_fixed_and_carries_nothing_from_the_request(
        self, config: RunnerConfig, kind: str
    ) -> None:
        spec = scan_spec(config, scan_request(config, kind), config.scanners[kind])
        assert spec.command == ("/usr/local/bin/nave-scan",)
        assert spec.image == config.scanners[kind].reference

    def test_only_trivy_gets_the_read_only_database_volume(self, config: RunnerConfig) -> None:
        semgrep = render_run_arguments(
            scan_spec(config, scan_request(config), config.scanners["semgrep"])
        )
        trivy = render_run_arguments(
            scan_spec(config, scan_request(config, "trivy"), config.scanners["trivy"])
        )
        db = "type=volume,src=nave-trivy-db,dst=/trivy-db,readonly"
        assert db in pairs(trivy, "--mount")
        assert db not in pairs(semgrep, "--mount")

    def test_the_scanner_image_comes_from_the_config_not_from_the_request(self) -> None:
        config = make_config()
        spec = scan_spec(config, scan_request(config), config.scanners["semgrep"])
        assert spec.image.endswith("nave-semgrep@sha256:" + "d" * 64)


class TestWorkspaceVolume:
    def test_is_a_bounded_non_executable_tmpfs_owned_by_the_unprivileged_user(self) -> None:
        args = render_volume_arguments(RUN, 1 << 30)
        o = next(a for a in args if a.startswith("o="))
        assert "type=tmpfs" in args
        assert "device=tmpfs" in args
        assert "size=1073741824" in o
        assert "noexec" in o
        assert "nosuid" in o
        assert "uid=65532" in o
        assert "mode=0700" in o
        assert args[-1] == workspace_volume(RUN)

    def test_names_derive_only_from_the_validated_run_id(self) -> None:
        assert workspace_volume(RUN) == f"nave-ws-{RUN}"


def test_runtime_and_seccomp_are_omitted_only_when_unset() -> None:
    config = make_config(runtime=None, seccomp_profile=None)
    rendered = render_run_arguments(fetch_spec(config, fetch_request(config)))
    assert "--runtime" not in rendered
    assert not any(a.startswith("seccomp=") for a in pairs(rendered, "--security-opt"))
    assert "no-new-privileges:true" in pairs(rendered, "--security-opt")
