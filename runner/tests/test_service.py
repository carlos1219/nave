import base64
import contextlib
import threading

import pytest

from nave_runner.config import RunnerConfig
from nave_runner.engine import EngineError, RunResult
from nave_runner.schema import FetchRequest, ReleaseRequest, ScanRequest
from nave_runner.service import RunnerError, RunnerService
from tests.conftest import DIGEST, RUN, TOKEN, FakeEngine

FETCH = FetchRequest(RUN, "acme", "app", "a" * 40, TOKEN, 1000, 100, 60)
SCAN = ScanRequest(RUN, "semgrep", 600, 1 << 20)


def result(
    code: int | None = 0, out: bytes = b"", *, timeout: bool = False, over: bool = False
) -> RunResult:
    return RunResult(code, out, timeout, over)


def code_of(service: RunnerService, request: FetchRequest | ScanRequest | ReleaseRequest) -> str:
    with pytest.raises(RunnerError) as raised:
        service.handle(request)
    return raised.value.code


@pytest.fixture
def service(config: RunnerConfig, engine: FakeEngine) -> RunnerService:
    return RunnerService(config, engine)


class TestFetch:
    def test_success_keeps_the_workspace_and_passes_the_token_on_stdin(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        assert service.handle(FETCH) == {"status": "ok"}
        spec, stdin, timeout, _ = engine.runs[0]
        assert RUN in engine.workspaces
        assert stdin == (TOKEN + "\n").encode()
        assert TOKEN not in " ".join(spec.command)
        assert timeout > FETCH.timeout_seconds

    @pytest.mark.parametrize(
        ("run_result", "expected"),
        [
            (result(10), "too_large"),
            (result(11), "fetch_failed"),
            (result(12), "fetch_unavailable"),
            (result(1), "internal"),
            (result(137), "internal"),
            (result(None), "internal"),
            (result(None, timeout=True), "timeout"),
        ],
    )
    def test_failures_map_to_protocol_codes_and_free_the_workspace(
        self, service: RunnerService, engine: FakeEngine, run_result: RunResult, expected: str
    ) -> None:
        engine.results.append(run_result)
        assert code_of(service, FETCH) == expected
        assert RUN not in engine.workspaces

    def test_engine_failure_is_transient_and_cleans_up(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        engine.results.append(EngineError("docker down"))
        assert code_of(service, FETCH) == "fetch_unavailable"
        assert RUN not in engine.workspaces

    def test_a_holder_keeps_the_workspace_mounted_for_the_whole_run(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        # Sin sostenedor el volumen tmpfs se vacía al acabar el fetch (ADR 0025).
        service.handle(FETCH)
        assert [spec.name for spec in engine.holders] == [f"nave-hold-{RUN}"]
        assert engine.holders[0].detach
        assert engine.holders[0].network == "none"

    def test_the_holder_starts_before_the_fetch_writes_anything(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        engine.fail_holder = True
        assert code_of(service, FETCH) == "fetch_unavailable"
        assert engine.runs == []
        assert RUN not in engine.workspaces

    def test_failure_to_create_the_workspace_is_transient(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        engine.fail_create = True
        assert code_of(service, FETCH) == "fetch_unavailable"

    def test_a_run_id_cannot_be_fetched_twice(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        service.handle(FETCH)
        assert code_of(service, FETCH) == "bad_request"
        assert len(engine.runs) == 1

    def test_a_stuck_cleanup_does_not_hide_the_original_error(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        engine.results.append(result(11))
        engine.fail_remove = True
        assert code_of(service, FETCH) == "fetch_failed"


class TestScan:
    def test_returns_the_configured_version_and_digest_with_the_output(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        engine.workspaces.add(RUN)
        engine.results.append(result(0, b'{"runs":[]}'))
        reply = service.handle(SCAN)
        assert reply["scanner_version"] == "1.180.0"
        assert reply["image_digest"] == DIGEST
        assert reply["exit_code"] == 0
        assert base64.b64decode(str(reply["stdout_b64"])) == b'{"runs":[]}'
        _, stdin, timeout, max_output = engine.runs[0]
        assert (stdin, timeout, max_output) == (None, 600, 1 << 20)

    def test_scan_does_not_remove_the_workspace(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        engine.workspaces.add(RUN)
        service.handle(SCAN)
        assert RUN in engine.workspaces

    def test_unknown_workspace(self, service: RunnerService) -> None:
        assert code_of(service, SCAN) == "unknown_workspace"

    def test_a_workspace_whose_holder_died_is_unknown_not_empty(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        # Sin sostenedor el contenido ya se perdió: escanear un árbol vacío daría un falso PASS.
        engine.workspaces.add(RUN)
        engine.dead_holders.add(RUN)
        assert code_of(service, SCAN) == "unknown_workspace"
        assert engine.runs == []

    @pytest.mark.parametrize(
        ("run_result", "expected"),
        [
            (result(None, timeout=True), "timeout"),
            (result(None, over=True), "output_too_large"),
            (result(1), "scanner_failed"),
            (result(2), "scanner_failed"),
            (result(137), "scanner_failed"),
            (result(None), "scanner_failed"),
        ],
    )
    def test_failures(
        self, service: RunnerService, engine: FakeEngine, run_result: RunResult, expected: str
    ) -> None:
        engine.workspaces.add(RUN)
        engine.results.append(run_result)
        assert code_of(service, SCAN) == expected

    def test_engine_failure_is_internal(self, service: RunnerService, engine: FakeEngine) -> None:
        engine.workspaces.add(RUN)
        engine.results.append(EngineError("down"))
        assert code_of(service, SCAN) == "internal"


class TestRelease:
    def test_is_idempotent(self, service: RunnerService, engine: FakeEngine) -> None:
        engine.workspaces.add(RUN)
        assert service.handle(ReleaseRequest(RUN)) == {"status": "ok"}
        assert service.handle(ReleaseRequest(RUN)) == {"status": "ok"}
        assert RUN not in engine.workspaces

    def test_a_stuck_removal_is_reported(self, service: RunnerService, engine: FakeEngine) -> None:
        engine.fail_remove = True
        assert code_of(service, ReleaseRequest(RUN)) == "internal"

    def test_does_not_need_a_free_slot(self, config: RunnerConfig, engine: FakeEngine) -> None:
        service = RunnerService(config, engine)
        for _ in range(config.max_concurrent):
            service._slots.acquire()
        assert service.handle(ReleaseRequest(RUN)) == {"status": "ok"}


class TestBackpressure:
    def test_rejects_with_busy_when_all_slots_are_taken(
        self, config: RunnerConfig, engine: FakeEngine
    ) -> None:
        service = RunnerService(config, engine)
        engine.workspaces.add(RUN)
        for _ in range(config.max_concurrent):
            service._slots.acquire()
        assert code_of(service, SCAN) == "busy"
        assert code_of(service, FETCH) == "busy"
        assert not engine.runs

    def test_a_slot_is_released_after_success_and_failure(
        self, config: RunnerConfig, engine: FakeEngine
    ) -> None:
        service = RunnerService(config, engine)
        engine.workspaces.add(RUN)
        for outcome in [result(0), result(1), EngineError("x")] * 3:
            engine.results.append(outcome)
            with contextlib.suppress(RunnerError):
                service.handle(SCAN)
        service.handle(SCAN)

    def test_concurrent_requests_beyond_the_limit_are_rejected(
        self, config: RunnerConfig, engine: FakeEngine
    ) -> None:
        service = RunnerService(config, engine)
        engine.gate = threading.Event()
        engine.workspaces.add(RUN)
        threads = [
            threading.Thread(target=lambda: service.handle(SCAN))
            for _ in range(config.max_concurrent)
        ]
        for thread in threads:
            thread.start()
        deadline = 50
        while len(engine.runs) < config.max_concurrent and deadline:
            threading.Event().wait(0.05)
            deadline -= 1
        try:
            assert code_of(service, SCAN) == "busy"
        finally:
            engine.gate.set()
            for thread in threads:
                thread.join(timeout=5)


class TestSingleFlight:
    def test_a_run_id_cannot_be_fetched_concurrently(
        self, config: RunnerConfig, engine: FakeEngine
    ) -> None:
        service = RunnerService(config, engine)
        engine.gate = threading.Event()
        thread = threading.Thread(target=lambda: service.handle(FETCH))
        thread.start()
        assert engine.entered.wait(timeout=5)
        try:
            assert code_of(service, FETCH) == "bad_request"
        finally:
            engine.gate.set()
            thread.join(timeout=5)

    def test_the_claim_is_released_after_a_failure(
        self, service: RunnerService, engine: FakeEngine
    ) -> None:
        engine.results.append(result(11))
        assert code_of(service, FETCH) == "fetch_failed"
        assert service.handle(FETCH) == {"status": "ok"}
