"""Cliente del `scan-runner` (ADR 0002, ADR 0012).

El worker nunca habla con un motor de contenedores: solo envía peticiones firmadas con
el cuerpo cerrado de `protocol.py`. Toda respuesta del runner se valida con esquema
estricto y límite de tamaño antes de usarse (el runner está en una zona de menor
confianza que el worker: ejecuta el escáner sobre código del atacante).
"""

import base64
import binascii
import http.client
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Protocol
from urllib.parse import urlsplit

from nave.application.dto.scanning import FetchLimits, ScanLimits
from nave.application.errors import (
    ApplicationError,
    FetchUnavailableError,
    RepositoryFetchError,
    RepositoryTooLargeError,
    ScannerExecutionError,
    ScannerOutputInvalidError,
    ScannerUnavailableError,
    ScanTimeoutError,
    TransientError,
)
from nave.application.ports.system import Clock
from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.infrastructure.sandbox.protocol import (
    MAX_REQUEST_BYTES,
    RUN_ID_PATTERN,
    RUNS_PATH,
    TOKEN_PATTERN,
    ErrorCode,
    RunnerKey,
    Step,
    canonical_json,
    signed_headers,
)

_MAX_ERROR_BODY: Final = 4096
_MAX_RETRY_AFTER: Final = timedelta(minutes=5)
_HTTP_BAD_REQUEST: Final = 400
_HTTP_OK: Final = 200
_HTTP_UNAUTHORIZED: Final = 401
_HTTP_FORBIDDEN: Final = 403
_HTTP_TOO_MANY: Final = 429
_HTTP_SERVER_ERROR: Final = 500
_REQUEST_MARGIN: Final = timedelta(seconds=30)
_SCAN_REPLY_KEYS: Final = frozenset({"scanner_version", "image_digest", "exit_code", "stdout_b64"})


class TransportError(Exception):
    """No hubo respuesta HTTP utilizable (conexión, timeout, respuesta demasiado grande)."""


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    body: bytes
    retry_after: str | None = None


class Transport(Protocol):
    def post(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout: timedelta,
        max_response_bytes: int,
    ) -> TransportResponse: ...


class HttpTransport:
    """HTTP/1.1 con la biblioteca estándar. No sigue redirecciones ni lee más de
    `max_response_bytes`: un runner comprometido no puede agotar la memoria del worker."""

    def __init__(self, base_url: str) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise InvalidValueError("runner_url", "must be an http(s) URL with a host")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise InvalidValueError("runner_url", "must not carry credentials, query or fragment")
        if parts.path not in {"", "/"}:
            raise InvalidValueError("runner_url", "must not have a path")
        self._tls = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port or (443 if self._tls else 80)

    def post(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout: timedelta,
        max_response_bytes: int,
    ) -> TransportResponse:
        seconds = timeout.total_seconds()
        connection: http.client.HTTPConnection = (
            http.client.HTTPSConnection(self._host, self._port, timeout=seconds)
            if self._tls
            else http.client.HTTPConnection(self._host, self._port, timeout=seconds)
        )
        try:
            connection.request("POST", RUNS_PATH, body=body, headers=dict(headers))
            response = connection.getresponse()
            data = response.read(max_response_bytes + 1)
            if len(data) > max_response_bytes:
                raise TransportError("response too large")
            return TransportResponse(response.status, data, response.getheader("Retry-After"))
        except (OSError, http.client.HTTPException) as error:
            raise TransportError(type(error).__name__) from error
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class ScanReply:
    scanner_version: str
    image_digest: str
    exit_code: int
    stdout: bytes


class RunnerClient:
    def __init__(
        self,
        *,
        transport: Transport,
        key: RunnerKey,
        clock: Clock,
        new_nonce: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self._transport = transport
        self._key = key
        self._clock = clock
        self._new_nonce = new_nonce

    def fetch(
        self, *, run_id: str, repo: RepoRef, commit: CommitSha, token: str, limits: FetchLimits
    ) -> None:
        if not fullmatch_str(TOKEN_PATTERN, token):
            raise InvalidValueError("token", "is not a valid installation token")
        body = {
            "step": Step.FETCH.value,
            "run_id": _run_id(run_id),
            "owner": repo.owner,
            "name": repo.name,
            "commit_sha": commit.value,
            "token": token,
            "max_bytes": limits.max_bytes,
            "max_files": limits.max_files,
            "timeout_seconds": _seconds(limits.max_duration),
        }
        reply = self._send(
            Step.FETCH, body, timeout=limits.max_duration, max_response=_MAX_ERROR_BODY
        )
        if _json_object(reply.body) != {"status": "ok"}:
            raise RepositoryFetchError("invalid_runner_response")

    def scan(self, *, run_id: str, kind: ScannerKind, limits: ScanLimits) -> ScanReply:
        body = {
            "step": Step.SCAN.value,
            "run_id": _run_id(run_id),
            "scanner_kind": kind.value,
            "timeout_seconds": _seconds(limits.max_duration),
            "max_output_bytes": limits.max_output_bytes,
        }
        # base64 infla 4/3 y el JSON añade unos cientos de bytes.
        max_response = limits.max_output_bytes * 4 // 3 + 8192
        reply = self._send(Step.SCAN, body, timeout=limits.max_duration, max_response=max_response)
        return _parse_scan_reply(reply.body, limits.max_output_bytes)

    def release(self, *, run_id: str) -> None:
        body = {"step": Step.RELEASE.value, "run_id": _run_id(run_id)}
        self._send(Step.RELEASE, body, timeout=timedelta(seconds=30), max_response=_MAX_ERROR_BODY)

    def _send(
        self, step: Step, fields: Mapping[str, object], *, timeout: timedelta, max_response: int
    ) -> TransportResponse:
        payload = canonical_json(fields)
        if len(payload) > MAX_REQUEST_BYTES:
            raise InvalidValueError("request", "exceeds the protocol size limit")
        headers = signed_headers(
            self._key,
            timestamp=int(self._clock.now().timestamp()),
            nonce=self._new_nonce(),
            body=payload,
        )
        try:
            response = self._transport.post(
                headers=headers,
                body=payload,
                timeout=timeout + _REQUEST_MARGIN,
                max_response_bytes=max_response,
            )
        except TransportError as error:
            raise _unavailable(step, str(error)) from error
        if response.status == _HTTP_OK:
            return response
        raise _map_error(step, response)


def _run_id(value: str) -> str:
    if not fullmatch_str(RUN_ID_PATTERN, value):
        raise InvalidValueError("run_id", "must be 32 lowercase hex characters")
    return value


def _seconds(value: timedelta) -> int:
    return max(1, int(value.total_seconds()))


def _unavailable(step: Step, detail: str, retry_after: timedelta | None = None) -> TransientError:
    error_type = ScannerUnavailableError if step is Step.SCAN else FetchUnavailableError
    return error_type(f"runner unavailable: {detail}", retry_after=retry_after)


def _retry_after(raw: str | None) -> timedelta | None:
    if raw is None or not raw.isascii() or not raw.isdigit() or len(raw) > 6:  # noqa: PLR2004
        return None
    return min(timedelta(seconds=int(raw)), _MAX_RETRY_AFTER)


def _json_object(data: bytes) -> dict[str, object] | None:
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _map_error(step: Step, response: TransportResponse) -> ApplicationError:  # noqa: PLR0911
    body = _json_object(response.body) or {}
    raw_code = body.get("error")
    try:
        code = ErrorCode(raw_code) if isinstance(raw_code, str) else None
    except ValueError:
        code = None
    retry_after = _retry_after(response.retry_after)
    status = response.status
    if code is ErrorCode.BUSY or status == _HTTP_TOO_MANY:
        return _unavailable(step, "busy", retry_after)
    if code is ErrorCode.UNAUTHORIZED or status in {_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN}:
        # Clave mal desplegada o reloj desfasado: reintentar y, si persiste, abandonar.
        return _unavailable(step, "unauthorized")
    if code is ErrorCode.BAD_REQUEST or status == _HTTP_BAD_REQUEST:
        permanent = ScannerExecutionError if step is Step.SCAN else RepositoryFetchError
        return permanent("runner_rejected_request")
    if code is ErrorCode.TOO_LARGE:
        return RepositoryTooLargeError("too_large")
    if code is ErrorCode.FETCH_FAILED:
        return RepositoryFetchError("fetch_failed")
    if code is ErrorCode.TIMEOUT:
        return ScanTimeoutError() if step is Step.SCAN else _unavailable(step, "fetch timeout")
    if code is ErrorCode.OUTPUT_TOO_LARGE:
        return ScannerOutputInvalidError("output_too_large")
    if code is ErrorCode.SCANNER_FAILED:
        return ScannerExecutionError("scanner_failed")
    if code is ErrorCode.UNKNOWN_WORKSPACE:
        return _unavailable(step, "workspace is gone")
    # fetch_unavailable, internal, 5xx o cualquier cosa inesperada.
    return _unavailable(step, f"status {status}", retry_after)


def _parse_scan_reply(data: bytes, max_output_bytes: int) -> ScanReply:
    body = _json_object(data)
    if body is None or set(body) != _SCAN_REPLY_KEYS:
        raise ScannerOutputInvalidError("invalid_runner_response")
    version, digest = body["scanner_version"], body["image_digest"]
    exit_code, encoded = body["exit_code"], body["stdout_b64"]
    if (
        not isinstance(version, str)
        or not isinstance(digest, str)
        or not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or not isinstance(encoded, str)
    ):
        raise ScannerOutputInvalidError("invalid_runner_response")
    try:
        stdout = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ScannerOutputInvalidError("invalid_runner_response") from error
    if len(stdout) > max_output_bytes:
        raise ScannerOutputInvalidError("output_too_large")
    return ScanReply(version, digest, exit_code, stdout)
