"""Test cruzado: el cliente del worker contra el servidor real del runner.

Son dos implementaciones independientes del protocolo (ADR 0012); aquí se comprueba que
interoperan de extremo a extremo por HTTP real, con un motor de contenedores simulado.
"""

import base64
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import timedelta

import pytest
from nave_runner.config import DEFAULT_MAX_OUTPUT_BYTES, RunnerConfig, ScannerImage
from nave_runner.engine import EngineError, RunResult
from nave_runner.server import RunnerHTTPServer
from nave_runner.templates import ContainerSpec

from nave.application.dto.workspace import WorkspaceHandle
from nave.application.errors import (
    FetchUnavailableError,
    RepositoryFetchError,
    RepositoryTooLargeError,
    ScannerExecutionError,
    ScannerUnavailableError,
    ScanTimeoutError,
)
from nave.bootstrap.config import DEFAULT_SCANNER_MAX_OUTPUT_BYTES
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.sandbox.protocol import RunnerKey
from nave.infrastructure.sandbox.runner_client import HttpTransport, RunnerClient
from nave.infrastructure.sandbox.runner_fetcher import RunnerRepositoryFetcher
from nave.infrastructure.scanners.semgrep import semgrep_scanner
from nave.infrastructure.system import SystemClock
from tests.fakes import sarif as s
from tests.unit.application.builders import FETCH_LIMITS, SCAN_LIMITS

KEY_HEX = "5a" * 32
DIGEST = "sha256:" + "e" * 64
TOKEN = "ghs_" + "R" * 36


class SimulatedEngine:
    def __init__(self) -> None:
        self.workspaces: set[str] = set()
        self.holders: set[str] = set()
        self.specs: list[tuple[ContainerSpec, bytes | None]] = []
        self.results: list[RunResult | Exception] = []

    def create_workspace(self, run_id: str, *, size_bytes: int) -> None:
        self.workspaces.add(run_id)

    def start_holder(self, spec: ContainerSpec) -> None:
        # El sostenedor mantiene montado el volumen tmpfs entre el fetch y el escaneo (ADR 0025).
        self.holders.add(spec.name)

    def workspace_exists(self, run_id: str) -> bool:
        return run_id in self.workspaces

    def workspace_ready(self, run_id: str) -> bool:
        return run_id in self.workspaces and f"nave-hold-{run_id}" in self.holders

    def remove_workspace(self, run_id: str) -> None:
        self.workspaces.discard(run_id)
        self.holders.discard(f"nave-hold-{run_id}")

    def run(
        self, spec: ContainerSpec, *, stdin: bytes | None, timeout_seconds: int, max_output: int
    ) -> RunResult:
        self.specs.append((spec, stdin))
        outcome = self.results.pop(0) if self.results else RunResult(0, b"", False, False)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def kill_run(self, run_id: str) -> None: ...

    def list_orphans(self) -> list[object]:
        return []

    def remove_orphan(self, orphan: object) -> None: ...

    def ping(self) -> bool:
        return True


class Tokens:
    @contextmanager
    def fetch_token(self, *, installation_id: int, repo: RepoRef) -> Iterator[str]:
        yield TOKEN


def runner_config(**overrides: object) -> RunnerConfig:
    base: dict[str, object] = {
        "key": bytes.fromhex(KEY_HEX),
        "listen_host": "127.0.0.1",
        "listen_port": 0,
        "engine_binary": "/usr/local/bin/docker",
        "runtime": None,
        "seccomp_profile": None,
        "fetch_image": f"registry.internal/fetcher@{DIGEST}",
        "scanners": {
            "semgrep": ScannerImage(
                "semgrep", f"registry.internal/semgrep@{DIGEST}", DIGEST, "1.180.0"
            )
        },
        "egress_network": "nave-egress",
        "egress_proxy_url": "http://egress-proxy:3128",
        "trivy_db_volume": "nave-trivy-db",
        "workspace_bytes": 2 << 30,
        "max_fetch_bytes": 2 << 30,
        "max_fetch_files": 1_000_000,
        "max_fetch_seconds": 3600,
        "max_scan_seconds": 7200,
        "max_output_bytes": 64 << 20,
        "max_concurrent": 1,
        "orphan_max_age_seconds": 3 * 3600,
        "sweep_interval_seconds": 300,
    }
    base.update(overrides)
    return RunnerConfig(**base)  # type: ignore[arg-type]


class World:
    def __init__(self, server: RunnerHTTPServer, engine: SimulatedEngine) -> None:
        self.server = server
        self.engine = engine
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def client(self, key_hex: str = KEY_HEX) -> RunnerClient:
        return RunnerClient(
            transport=HttpTransport(self.url), key=RunnerKey.from_hex(key_hex), clock=SystemClock()
        )


@pytest.fixture
def world() -> Iterator[World]:
    engine = SimulatedEngine()
    server = RunnerHTTPServer(runner_config(), engine)  # type: ignore[arg-type]
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield World(server, engine)
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


REPO = RepoRef("acme", "app")
SHA = CommitSha("c" * 40)


def checkout(world: World, key_hex: str = KEY_HEX) -> AbstractContextManager[WorkspaceHandle]:
    fetcher = RunnerRepositoryFetcher(client=world.client(key_hex), tokens=Tokens())
    return fetcher.checkout(installation_id=1, repo=REPO, commit=SHA, limits=FETCH_LIMITS)


def test_worker_defaults_fit_within_runner_defaults() -> None:
    # Con 256 MiB en el worker y 64 MiB en el runner, el runner rechazaba con 400 todo
    # escaneo real; los tests usaban límites propios y no lo veían (fase 7, F7-32).
    assert DEFAULT_SCANNER_MAX_OUTPUT_BYTES <= DEFAULT_MAX_OUTPUT_BYTES


def test_fetch_scan_release_end_to_end(world: World) -> None:
    sarif = s.semgrep_sample()
    world.engine.results = [RunResult(0, b"", False, False), RunResult(0, sarif, False, False)]
    scanner = semgrep_scanner(world.client(), expected_version="1.180.0")
    with checkout(world) as workspace:
        assert workspace.value in world.engine.workspaces
        output = scanner.scan(workspace, SCAN_LIMITS)
    assert not world.engine.workspaces
    assert output.results[0].rule_id.startswith("python.lang")
    assert output.image_digest == DIGEST
    fetch_spec, stdin = world.engine.specs[0]
    assert stdin == (TOKEN + "\n").encode()
    assert TOKEN not in " ".join(fetch_spec.command)
    assert f"--owner={REPO.owner}" in fetch_spec.command
    scan_spec, scan_stdin = world.engine.specs[1]
    assert (scan_spec.network, scan_stdin) == ("none", None)


def test_a_wrong_key_is_a_transient_failure_and_runs_nothing(world: World) -> None:
    with pytest.raises(FetchUnavailableError), checkout(world, key_hex="11" * 32):
        pass
    assert not world.engine.specs


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(10, RepositoryTooLargeError), (11, RepositoryFetchError), (12, FetchUnavailableError)],
)
def test_fetch_exit_codes_reach_the_worker_as_typed_errors(
    world: World, exit_code: int, expected: type[Exception]
) -> None:
    world.engine.results = [RunResult(exit_code, b"", False, False)]
    with pytest.raises(expected), checkout(world):
        pass
    assert not world.engine.workspaces


def test_scan_failures_reach_the_worker_as_typed_errors(world: World) -> None:
    scanner = semgrep_scanner(world.client(), expected_version="1.180.0")
    with checkout(world) as workspace:
        world.engine.results = [RunResult(None, b"", True, False)]
        with pytest.raises(ScanTimeoutError):
            scanner.scan(workspace, SCAN_LIMITS)
        world.engine.results = [RunResult(1, b"", False, False)]
        with pytest.raises(ScannerExecutionError):
            scanner.scan(workspace, SCAN_LIMITS)
        world.engine.results = [EngineError("down")]
        with pytest.raises(ScannerUnavailableError):
            scanner.scan(workspace, SCAN_LIMITS)


def test_scanning_a_released_workspace_is_a_transient_error(world: World) -> None:
    scanner = semgrep_scanner(world.client(), expected_version="1.180.0")
    with pytest.raises(ScannerUnavailableError):
        scanner.scan(WorkspaceHandle("0" * 32), SCAN_LIMITS)


def test_backpressure_carries_retry_after_to_the_worker(world: World) -> None:
    world.server.service._slots.acquire()
    with pytest.raises(FetchUnavailableError) as raised, checkout(world):
        pass
    assert raised.value.retry_after == timedelta(seconds=15)


def test_a_scanner_version_the_runner_does_not_report_is_refused(world: World) -> None:
    world.engine.results = [
        RunResult(0, b"", False, False),
        RunResult(0, s.semgrep_sample(), False, False),
    ]
    scanner = semgrep_scanner(world.client(), expected_version="9.9.9")
    with checkout(world) as workspace, pytest.raises(ScannerExecutionError):
        scanner.scan(workspace, SCAN_LIMITS)


def test_large_outputs_survive_base64_and_the_response_cap(world: World) -> None:
    results = [s.result(f"rule.{i}", "m" * 200, uri="repo/a.py") for i in range(3000)]
    big = s.document([], results)
    assert len(big) > 600_000
    world.engine.results = [RunResult(0, b"", False, False), RunResult(0, big, False, False)]
    scanner = semgrep_scanner(world.client(), expected_version="1.180.0")
    with checkout(world) as workspace:
        output = scanner.scan(workspace, SCAN_LIMITS)
    assert len(output.results) == 3000
    assert base64.b64decode(base64.b64encode(big)) == big
