import threading
from datetime import UTC, datetime, timedelta

import pytest

from nave_runner.config import RunnerConfig, ScannerImage
from nave_runner.engine import EngineError, Orphan, RunResult
from nave_runner.templates import ContainerSpec

KEY = bytes(range(32))
RUN = "0123456789abcdef0123456789abcdef"
DIGEST = "sha256:" + "d" * 64
TOKEN = "ghs_" + "T" * 36
# Formato nuevo (ADR 0013): ~520 caracteres, con puntos; se trata como opaco.
LONG_TOKEN = "ghs_12345678_" + "eyJhbGciOiJSUzI1NiJ9." + "A" * 460 + "." + "B" * 43


def make_config(**overrides: object) -> RunnerConfig:
    base: dict[str, object] = {
        "key": KEY,
        "listen_host": "127.0.0.1",
        "listen_port": 0,
        "engine_binary": "/usr/local/bin/docker",
        "runtime": "runsc",
        "seccomp_profile": "/etc/nave/seccomp.json",
        "fetch_image": f"registry.internal/nave-fetcher@{DIGEST}",
        "scanners": {
            "semgrep": ScannerImage(
                "semgrep", f"registry.internal/nave-semgrep@{DIGEST}", DIGEST, "1.180.0"
            ),
            "trivy": ScannerImage(
                "trivy", f"registry.internal/nave-trivy@{DIGEST}", DIGEST, "0.58.0"
            ),
        },
        "egress_network": "nave-egress",
        "egress_proxy_url": "http://egress-proxy:3128",
        "trivy_db_volume": "nave-trivy-db",
        "workspace_bytes": 1 << 30,
        "max_fetch_bytes": 1 << 30,
        "max_fetch_files": 200_000,
        "max_fetch_seconds": 3600,
        "max_scan_seconds": 7200,
        "max_output_bytes": 64 << 20,
        "max_concurrent": 2,
        "orphan_max_age_seconds": 3 * 3600,
        "sweep_interval_seconds": 300,
    }
    base.update(overrides)
    return RunnerConfig(**base)  # type: ignore[arg-type]


@pytest.fixture
def config() -> RunnerConfig:
    return make_config()


class FakeEngine:
    def __init__(self) -> None:
        self.workspaces: set[str] = set()
        self.runs: list[tuple[ContainerSpec, bytes | None, int, int]] = []
        self.results: list[RunResult | Exception] = []
        self.removed: list[str] = []
        self.orphans: list[Orphan] = []
        self.removed_orphans: list[Orphan] = []
        self.fail_remove: bool = False
        self.fail_create: bool = False
        self.fail_holder: bool = False
        self.holders: list[ContainerSpec] = []
        self.dead_holders: set[str] = set()
        self.healthy = True
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def create_workspace(self, run_id: str, *, size_bytes: int) -> None:
        if self.fail_create:
            raise EngineError("down")
        self.workspaces.add(run_id)

    def start_holder(self, spec: ContainerSpec) -> None:
        if self.fail_holder:
            raise EngineError("holder down")
        self.holders.append(spec)

    def workspace_exists(self, run_id: str) -> bool:
        return run_id in self.workspaces

    def workspace_ready(self, run_id: str) -> bool:
        return run_id in self.workspaces and run_id not in self.dead_holders

    def remove_workspace(self, run_id: str) -> None:
        if self.fail_remove:
            raise EngineError("stuck")
        self.workspaces.discard(run_id)
        self.removed.append(run_id)

    def run(
        self, spec: ContainerSpec, *, stdin: bytes | None, timeout_seconds: int, max_output: int
    ) -> RunResult:
        self.runs.append((spec, stdin, timeout_seconds, max_output))
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(timeout=5)
        result = self.results.pop(0) if self.results else RunResult(0, b"", False, False)
        if isinstance(result, Exception):
            raise result
        return result

    def kill_run(self, run_id: str) -> None:
        return

    def list_orphans(self) -> list[Orphan]:
        return list(self.orphans)

    def remove_orphan(self, orphan: Orphan) -> None:
        if self.fail_remove:
            raise EngineError("stuck")
        self.removed_orphans.append(orphan)

    def ping(self) -> bool:
        return self.healthy


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
