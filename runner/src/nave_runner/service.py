"""Lógica de las tres operaciones del runner (fetch, scan, release).

Sin HTTP aquí: recibe peticiones ya autenticadas y validadas y devuelve un resultado
o lanza `RunnerError` con un código del protocolo.
"""

import base64
import logging
import threading
import time
from typing import Final

from nave_runner.config import RunnerConfig
from nave_runner.engine import Engine, EngineError, RunResult
from nave_runner.schema import FetchRequest, ReleaseRequest, Request, ScanRequest
from nave_runner.templates import (
    EXIT_FETCH_FAILED,
    EXIT_FETCH_TOO_LARGE,
    EXIT_FETCH_UNAVAILABLE,
    SCAN_OK_EXIT_CODES,
    fetch_spec,
    hold_spec,
    scan_spec,
)

_LOG = logging.getLogger("nave_runner.service")
_KILL_MARGIN: Final = 15
RETRY_AFTER_SECONDS: Final = 15

HTTP_STATUS: Final = {
    "busy": 429,
    "bad_request": 400,
    "unauthorized": 401,
    "unknown_workspace": 404,
    "too_large": 413,
    "output_too_large": 413,
    "fetch_failed": 422,
    "scanner_failed": 422,
    "fetch_unavailable": 503,
    "timeout": 504,
    "internal": 500,
}


class RunnerError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RunnerService:
    def __init__(self, config: RunnerConfig, engine: Engine) -> None:
        self._config = config
        self._engine = engine
        self._slots = threading.BoundedSemaphore(config.max_concurrent)
        self._active: set[str] = set()
        self._active_lock = threading.Lock()

    def handle(self, request: Request) -> dict[str, object]:
        started = time.monotonic()
        step = type(request).__name__.removesuffix("Request").lower()
        outcome = "ok"
        try:
            if isinstance(request, FetchRequest):
                return self._fetch(request)
            if isinstance(request, ScanRequest):
                return self._scan(request)
            return self._release(request)
        except RunnerError as error:
            outcome = error.code
            raise
        finally:
            _LOG.info(
                "request handled",
                extra={
                    "run_id": request.run_id,
                    "step": step,
                    "outcome": outcome,
                    "duration_s": round(time.monotonic() - started, 3),
                },
            )

    def _acquire(self) -> None:
        # Sin cola: rechazar con 429 deja el control de la espera en el worker, que
        # reintenta con backoff y respeta Retry-After (AM-404).
        if not self._slots.acquire(blocking=False):
            raise RunnerError("busy")

    def _claim(self, run_id: str) -> None:
        """Un `run_id` solo puede tener un fetch en curso: dos clonados a la vez sobre el
        mismo volumen mezclarían ficheros de dos commits."""
        with self._active_lock:
            if run_id in self._active:
                raise RunnerError("bad_request")
            self._active.add(run_id)

    def _unclaim(self, run_id: str) -> None:
        with self._active_lock:
            self._active.discard(run_id)

    def _fetch(self, request: FetchRequest) -> dict[str, object]:
        self._claim(request.run_id)
        try:
            return self._fetch_claimed(request)
        finally:
            self._unclaim(request.run_id)

    def _fetch_claimed(self, request: FetchRequest) -> dict[str, object]:
        self._acquire()
        try:
            try:
                if self._engine.workspace_exists(request.run_id):
                    raise RunnerError("bad_request")
                self._engine.create_workspace(
                    request.run_id, size_bytes=self._config.workspace_bytes
                )
                # Sin sostenedor el volumen se desmonta al acabar el fetch y se vacía (ADR 0025).
                self._engine.start_holder(hold_spec(self._config, request.run_id))
                result = self._engine.run(
                    fetch_spec(self._config, request),
                    stdin=(request.token + "\n").encode("ascii"),
                    timeout_seconds=request.timeout_seconds + _KILL_MARGIN,
                    max_output=1,
                )
            except EngineError as error:
                self._discard(request.run_id)
                raise RunnerError("fetch_unavailable") from error
            code = _fetch_outcome(result)
            if code is not None:
                self._discard(request.run_id)
                raise RunnerError(code)
            return {"status": "ok"}
        finally:
            self._slots.release()

    def _scan(self, request: ScanRequest) -> dict[str, object]:
        scanner = self._config.scanners[request.scanner_kind]
        self._acquire()
        try:
            try:
                if not self._engine.workspace_ready(request.run_id):
                    raise RunnerError("unknown_workspace")
                result = self._engine.run(
                    scan_spec(self._config, request, scanner),
                    stdin=None,
                    timeout_seconds=request.timeout_seconds,
                    max_output=request.max_output_bytes,
                )
            except EngineError as error:
                raise RunnerError("internal") from error
        finally:
            self._slots.release()
        if result.timed_out:
            raise RunnerError("timeout")
        if result.output_exceeded:
            raise RunnerError("output_too_large")
        if result.exit_code not in SCAN_OK_EXIT_CODES:
            raise RunnerError("scanner_failed")
        return {
            "scanner_version": scanner.version,
            "image_digest": scanner.digest,
            "exit_code": result.exit_code,
            "stdout_b64": base64.b64encode(result.stdout).decode("ascii"),
        }

    def _release(self, request: ReleaseRequest) -> dict[str, object]:
        try:
            self._engine.remove_workspace(request.run_id)
        except EngineError as error:
            # El barrido de huérfanos reintenta; el worker vuelve a intentarlo si quiere.
            raise RunnerError("internal") from error
        return {"status": "ok"}

    def _discard(self, run_id: str) -> None:
        try:
            self._engine.remove_workspace(run_id)
        except EngineError:
            _LOG.warning("workspace cleanup postponed", extra={"run_id": run_id})


def _fetch_outcome(result: RunResult) -> str | None:
    if result.timed_out:
        return "timeout"
    return {
        0: None,
        EXIT_FETCH_TOO_LARGE: "too_large",
        EXIT_FETCH_FAILED: "fetch_failed",
        EXIT_FETCH_UNAVAILABLE: "fetch_unavailable",
    }.get(result.exit_code if result.exit_code is not None else -1, "internal")
