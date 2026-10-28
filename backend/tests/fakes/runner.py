"""Dobles del transporte y del cliente del scan-runner."""

import base64
import json
from collections.abc import Mapping
from datetime import timedelta

from nave.infrastructure.sandbox.protocol import RunnerKey
from nave.infrastructure.sandbox.runner_client import (
    RunnerClient,
    TransportError,
    TransportResponse,
)
from tests.fakes.memory import FakeClock

KEY = RunnerKey.from_hex("ab" * 32)
RUN = "0123456789abcdef0123456789abcdef"
DIGEST = "sha256:" + "d" * 64


class FakeTransport:
    def __init__(self, *responses: TransportResponse | TransportError) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Mapping[str, str], bytes, timedelta, int]] = []

    def post(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout: timedelta,
        max_response_bytes: int,
    ) -> TransportResponse:
        self.calls.append((headers, body, timeout, max_response_bytes))
        response = self.responses.pop(0)
        if isinstance(response, TransportError):
            raise response
        return response


def client(*responses: TransportResponse | TransportError) -> tuple[RunnerClient, FakeTransport]:
    transport = FakeTransport(*responses)
    return (
        RunnerClient(transport=transport, key=KEY, clock=FakeClock(), new_nonce=lambda: "0f" * 16),
        transport,
    )


def ok(payload: object = None) -> TransportResponse:
    return TransportResponse(
        200, json.dumps({"status": "ok"} if payload is None else payload).encode()
    )


def error(status: int, code: str, retry_after: str | None = None) -> TransportResponse:
    return TransportResponse(status, json.dumps({"error": code}).encode(), retry_after)


def scan_reply(**overrides: object) -> dict[str, object]:
    reply: dict[str, object] = {
        "scanner_version": "1.2.3",
        "image_digest": DIGEST,
        "exit_code": 0,
        "stdout_b64": base64.b64encode(b"{}").decode(),
    }
    reply.update(overrides)
    return reply
