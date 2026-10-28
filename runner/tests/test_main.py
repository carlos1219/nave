import json
import logging
import signal
import threading
from pathlib import Path

import pytest

from nave_runner import __main__ as entrypoint
from nave_runner.config import ConfigurationError, read_secret_file
from nave_runner.engine import EngineError, Orphan
from nave_runner.sweeper import run_forever
from tests.conftest import DIGEST, HOUR, NOW, FakeEngine

KEY_HEX = "ab" * 32
IMAGE = f"registry.internal/img@{DIGEST}"


def valid_env(key_file: Path) -> dict[str, str]:
    return {
        "NAVE_RUNNER_HMAC_KEY_FILE": str(key_file),
        "NAVE_RUNNER_FETCH_IMAGE": IMAGE,
        "NAVE_RUNNER_SCANNERS": "semgrep",
        "NAVE_RUNNER_IMAGE_SEMGREP": IMAGE,
        "NAVE_RUNNER_VERSION_SEMGREP": "1.180.0",
        "NAVE_RUNNER_EGRESS_PROXY_URL": "http://egress-proxy:3128",
    }


class StubServer:
    instances: list["StubServer"] = []  # noqa: RUF012

    def __init__(self, config: object, engine: object) -> None:
        self.closed = False
        self.stopped = threading.Event()
        StubServer.instances.append(self)

    def serve_forever(self) -> None:
        signal_handler = signal.getsignal(signal.SIGTERM)
        assert callable(signal_handler)
        signal_handler(signal.SIGTERM, None)
        self.stopped.wait(timeout=5)

    def shutdown(self) -> None:
        self.stopped.set()

    def server_close(self) -> None:
        self.closed = True


def test_main_starts_serves_and_stops_on_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key"
    key.write_text(KEY_HEX)
    for name, value in valid_env(key).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entrypoint, "RunnerHTTPServer", StubServer)
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        assert entrypoint.main() == 0
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    assert StubServer.instances[-1].closed


def test_main_refuses_to_start_with_an_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in list(__import__("os").environ):
        if name.startswith("NAVE_RUNNER_"):
            monkeypatch.delenv(name)
    assert entrypoint.main() == 2
    assert "NAVE_RUNNER_HMAC_KEY_FILE" in capsys.readouterr().err


def test_log_lines_are_json_with_only_allowed_fields() -> None:
    record = logging.LogRecord("x", logging.INFO, "f", 1, "request handled", None, None)
    record.run_id = "r" * 32
    record.token = "ghs_secret"  # no está en la lista blanca
    line = json.loads(entrypoint._JsonFormatter().format(record))
    assert line["service"] == "nave-scan-runner"
    assert line["run_id"] == "r" * 32
    assert "token" not in line
    assert "ghs_secret" not in json.dumps(line)


def test_read_secret_file(tmp_path: Path) -> None:
    path = tmp_path / "s"
    path.write_text("  value\n")
    assert read_secret_file(path) == "value"
    with pytest.raises(ConfigurationError):
        read_secret_file(tmp_path / "missing")
    big = tmp_path / "big"
    big.write_text("x" * 5000)
    with pytest.raises(ConfigurationError):
        read_secret_file(big)


def test_the_sweeper_loop_removes_old_orphans_and_survives_engine_errors() -> None:
    engine = FakeEngine()
    engine.orphans = [Orphan("volume", "nave-ws-" + "a" * 32, NOW - 100 * HOUR)]
    calls = {"n": 0}
    original = engine.list_orphans

    def flaky() -> list[Orphan]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise EngineError("down")
        return original()

    engine.list_orphans = flaky  # type: ignore[method-assign]
    stop = threading.Event()
    thread = threading.Thread(
        target=run_forever,
        kwargs={"engine": engine, "max_age": HOUR, "interval_seconds": 0, "stop": stop},
    )
    thread.start()
    for _ in range(200):
        if engine.removed_orphans:
            break
        threading.Event().wait(0.01)
    stop.set()
    thread.join(timeout=5)
    assert engine.removed_orphans
    assert calls["n"] >= 2
