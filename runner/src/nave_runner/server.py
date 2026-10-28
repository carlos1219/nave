"""Servidor HTTP del runner: `POST /v1/runs` y `GET /healthz`.

Biblioteca estándar. Cabecera y cuerpo se leen con tope y plazo; no hay chunked, ni
keep-alive, ni redirecciones. Toda petición no autenticada recibe la misma respuesta.
"""

import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from nave_runner.config import RunnerConfig
from nave_runner.engine import Engine
from nave_runner.protocol import (
    MAX_BODY_BYTES,
    RUNS_PATH,
    AuthenticationError,
    NonceStore,
    NonceStoreFullError,
    verify_request,
)
from nave_runner.schema import BadRequestError, parse_request
from nave_runner.service import HTTP_STATUS, RETRY_AFTER_SECONDS, RunnerError, RunnerService

_LOG = logging.getLogger("nave_runner.http")
_SOCKET_TIMEOUT: Final = 10
_MAX_CONNECTIONS: Final = 64
_NONCE_CAPACITY: Final = 100_000
_NONCE_TTL: Final = 150


class RunnerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, config: RunnerConfig, engine: Engine) -> None:
        self.config = config
        self.engine = engine
        self.service = RunnerService(config, engine)
        self.nonces = NonceStore(
            ttl_seconds=_NONCE_TTL, capacity=_NONCE_CAPACITY, started_at=time.time()
        )
        self._connections = threading.BoundedSemaphore(_MAX_CONNECTIONS)
        super().__init__((config.listen_host, config.listen_port), _Handler)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: object
    ) -> None:
        # Tope de conexiones simultáneas: una inundación no crea hilos sin límite.
        if not self._connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: object
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()


class _Handler(BaseHTTPRequestHandler):
    server: RunnerHTTPServer
    server_version = "nave-runner"
    sys_version = ""
    timeout = _SOCKET_TIMEOUT

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        if self.path == "/healthz":
            ok = self.server.engine.ping()
            self._reply(200 if ok else 503, {"status": "ok" if ok else "engine_unavailable"})
        else:
            self._reply(404, {"error": "bad_request"})

    def do_POST(self) -> None:
        if self.path != RUNS_PATH:
            self._reply(404, {"error": "bad_request"})
            return
        body = self._read_body()
        if body is None:
            return
        try:
            verify_request(
                self.server.config.key,
                method="POST",
                path=RUNS_PATH,
                headers={
                    "X-Nave-Timestamp": self.headers.get("X-Nave-Timestamp"),
                    "X-Nave-Nonce": self.headers.get("X-Nave-Nonce"),
                    "X-Nave-Signature": self.headers.get("X-Nave-Signature"),
                },
                body=body,
                now=time.time(),
                nonces=self.server.nonces,
            )
        except AuthenticationError:
            self._reply(401, {"error": "unauthorized"})
            return
        except NonceStoreFullError:
            self._reply(429, {"error": "busy"}, retry_after=RETRY_AFTER_SECONDS)
            return
        try:
            request = parse_request(body, self.server.config)
            payload = self.server.service.handle(request)
        except BadRequestError as error:
            # El motivo solo nombra la regla incumplida, nunca valores; sin este aviso un
            # desajuste de configuración con el worker era invisible (F7-32).
            _LOG.warning("request rejected", extra={"outcome": str(error)})
            self._reply(400, {"error": "bad_request"})
        except RunnerError as error:
            retry = RETRY_AFTER_SECONDS if error.code == "busy" else None
            self._reply(HTTP_STATUS[error.code], {"error": error.code}, retry_after=retry)
        else:
            self._reply(200, payload)

    def _read_body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding") is not None:
            self._reply(400, {"error": "bad_request"})
            return None
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.isascii() or not raw.isdigit() or len(raw) > 6:  # noqa: PLR2004
            self._reply(400, {"error": "bad_request"})
            return None
        length = int(raw)
        if length > MAX_BODY_BYTES:
            self._reply(413, {"error": "bad_request"})
            return None
        try:
            body = self.rfile.read(length)
        except OSError:
            return None
        if len(body) != length:
            return None
        return body

    def _reply(
        self, status: int, payload: dict[str, object], *, retry_after: int | None = None
    ) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("ascii")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            if retry_after is not None:
                self.send_header("Retry-After", str(retry_after))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            _LOG.debug("client went away")
