"""Autenticación de las peticiones (ADR 0012, AM-401).

Implementación independiente de la del worker: ambas se validan contra los mismos
vectores (`contracts/runner-protocol-vectors.json`). Todo fallo de autenticación produce
la misma respuesta, sin indicar qué comprobación falló.
"""

import hashlib
import hmac
import re
import threading
from collections.abc import Mapping
from typing import Final

PROTOCOL_TAG: Final = b"NAVE-RUNNER-V1"
RUNS_PATH: Final = "/v1/runs"
HEADER_TIMESTAMP: Final = "X-Nave-Timestamp"
HEADER_NONCE: Final = "X-Nave-Nonce"
HEADER_SIGNATURE: Final = "X-Nave-Signature"
MAX_BODY_BYTES: Final = 4096
MAX_SKEW_SECONDS: Final = 60
MIN_KEY_BYTES: Final = 32

_TIMESTAMP: Final = re.compile(r"[0-9]{1,12}", re.ASCII)
_NONCE: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)
_SIGNATURE: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)
_KEY_HEX: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)


class AuthenticationError(Exception):
    """Petición no autenticada. El motivo no se comunica al cliente."""


class NonceStoreFullError(Exception):
    """Demasiados nonces vivos: se rechaza en lugar de olvidar uno (anti-replay)."""


def parse_key(value: str) -> bytes:
    if not _KEY_HEX.fullmatch(value):
        raise ValueError("runner key must be 64 lowercase hex characters")
    return bytes.fromhex(value)


def string_to_sign(*, method: str, path: str, timestamp: int, nonce: str, body: bytes) -> bytes:
    return b"\n".join(
        (
            PROTOCOL_TAG,
            method.encode("ascii"),
            path.encode("ascii"),
            str(timestamp).encode("ascii"),
            nonce.encode("ascii"),
            hashlib.sha256(body).hexdigest().encode("ascii"),
        )
    )


def compute_signature(  # noqa: PLR0913 - campos firmados del protocolo
    key: bytes, *, method: str, path: str, timestamp: int, nonce: str, body: bytes
) -> str:
    message = string_to_sign(method=method, path=path, timestamp=timestamp, nonce=nonce, body=body)
    return hmac.new(key, message, hashlib.sha256).hexdigest()


class NonceStore:
    """Nonces vistos dentro de la ventana de validez. Memoria acotada por `capacity`."""

    def __init__(self, *, ttl_seconds: int, capacity: int, started_at: float = 0.0) -> None:
        self._ttl = ttl_seconds
        self._capacity = capacity
        # Los nonces viven en memoria: tras un reinicio, una petición capturada antes (aún
        # dentro de la ventana de ±60 s) se podría reproducir. Se rechaza todo lo firmado
        # antes del arranque; el worker reintenta y firma de nuevo.
        self.started_at = int(started_at)
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def register(self, nonce: str, now: float) -> bool:
        """True si el nonce es nuevo. Los caducados se purgan al vuelo."""
        with self._lock:
            if len(self._seen) >= self._capacity // 2:
                self._seen = {n: exp for n, exp in self._seen.items() if exp > now}
            if nonce in self._seen and self._seen[nonce] > now:
                return False
            if len(self._seen) >= self._capacity:
                raise NonceStoreFullError
            self._seen[nonce] = now + self._ttl
            return True


def verify_request(  # noqa: PLR0913 - contexto de verificación
    key: bytes,
    *,
    method: str,
    path: str,
    headers: Mapping[str, str | None],
    body: bytes,
    now: float,
    nonces: NonceStore,
) -> None:
    timestamp_raw = headers.get(HEADER_TIMESTAMP)
    nonce = headers.get(HEADER_NONCE)
    signature = headers.get(HEADER_SIGNATURE)
    if (
        timestamp_raw is None
        or nonce is None
        or signature is None
        or not _TIMESTAMP.fullmatch(timestamp_raw)
        or not _NONCE.fullmatch(nonce)
        or not _SIGNATURE.fullmatch(signature)
    ):
        raise AuthenticationError
    timestamp = int(timestamp_raw)
    if abs(now - timestamp) > MAX_SKEW_SECONDS or timestamp < nonces.started_at:
        raise AuthenticationError
    expected = compute_signature(
        key, method=method, path=path, timestamp=timestamp, nonce=nonce, body=body
    )
    if not hmac.compare_digest(expected, signature):
        raise AuthenticationError
    # El nonce se registra solo con firma válida: quien no conoce la clave no puede
    # llenar el almacén.
    if not nonces.register(nonce, now):
        raise AuthenticationError
