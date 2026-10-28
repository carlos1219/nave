"""Protocolo worker ↔ scan-runner, versión 1 (ADR 0012, AM-401, AM-402).

Una sola operación, `POST /v1/runs`, con cuerpo JSON cerrado y autenticación HMAC-SHA256
sobre método, ruta, marca temporal, nonce y huella del cuerpo. Este módulo es la mitad del
worker; el runner (`runner/`) implementa la otra mitad sin importar `nave`. Ambas se
validan contra los mismos vectores de prueba (`contracts/runner-protocol-vectors.json`).
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str

PROTOCOL_TAG: Final = b"NAVE-RUNNER-V1"
RUNS_PATH: Final = "/v1/runs"
HEADER_TIMESTAMP: Final = "X-Nave-Timestamp"
HEADER_NONCE: Final = "X-Nave-Nonce"
HEADER_SIGNATURE: Final = "X-Nave-Signature"
MAX_REQUEST_BYTES: Final = 4096
MAX_CLOCK_SKEW_SECONDS: Final = 60
MIN_KEY_BYTES: Final = 32

_KEY_HEX: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)
NONCE_PATTERN: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)
RUN_ID_PATTERN: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)
TOKEN_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{20,1024}", re.ASCII)
SIGNATURE_PATTERN: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)


class Step(StrEnum):
    FETCH = "fetch"
    SCAN = "scan"
    RELEASE = "release"


class ErrorCode(StrEnum):
    BUSY = "busy"
    TOO_LARGE = "too_large"
    OUTPUT_TOO_LARGE = "output_too_large"
    FETCH_FAILED = "fetch_failed"
    FETCH_UNAVAILABLE = "fetch_unavailable"
    TIMEOUT = "timeout"
    SCANNER_FAILED = "scanner_failed"
    UNKNOWN_WORKSPACE = "unknown_workspace"
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class RunnerKey:
    """Clave compartida worker ↔ runner (Docker secret `runner_hmac_key`)."""

    material: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.material) < MIN_KEY_BYTES:
            raise InvalidValueError("runner_key", f"must be at least {MIN_KEY_BYTES} bytes")

    @classmethod
    def from_hex(cls, value: str) -> "RunnerKey":
        if not fullmatch_str(_KEY_HEX, value):
            raise InvalidValueError("runner_key", "must be 64 lowercase hex characters")
        return cls(bytes.fromhex(value))


def canonical_json(fields: Mapping[str, object]) -> bytes:
    """Serialización única: lo que se firma es exactamente lo que se envía."""
    return json.dumps(
        fields, separators=(",", ":"), sort_keys=True, ensure_ascii=True, allow_nan=False
    ).encode("ascii")


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


def sign(  # noqa: PLR0913 - campos firmados del protocolo
    key: RunnerKey, *, method: str, path: str, timestamp: int, nonce: str, body: bytes
) -> str:
    message = string_to_sign(method=method, path=path, timestamp=timestamp, nonce=nonce, body=body)
    return hmac.new(key.material, message, hashlib.sha256).hexdigest()


def signed_headers(key: RunnerKey, *, timestamp: int, nonce: str, body: bytes) -> dict[str, str]:
    if not fullmatch_str(NONCE_PATTERN, nonce):
        raise InvalidValueError("nonce", "must be 32 lowercase hex characters")
    return {
        "Content-Type": "application/json",
        HEADER_TIMESTAMP: str(timestamp),
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: sign(
            key, method="POST", path=RUNS_PATH, timestamp=timestamp, nonce=nonce, body=body
        ),
    }
