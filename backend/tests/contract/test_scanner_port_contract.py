"""Contrato común de todo `ScannerPort` (CLAUDE.md §17).

Cada implementación se construye con un runner simulado que devuelve una salida válida
propia y una hostil. Añadir un escáner nuevo = añadir una fila a `IMPLEMENTATIONS`.
"""

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from nave.application.dto.scanning import ScannerOutput
from nave.application.dto.workspace import WorkspaceHandle
from nave.application.errors import PermanentScanError
from nave.application.ports.scanning import ScannerPort
from nave.application.services.normalizer import normalize_output
from nave.infrastructure.sandbox.runner_client import RunnerClient
from nave.infrastructure.scanners.semgrep import semgrep_scanner
from nave.infrastructure.scanners.trivy import trivy_scanner
from tests.fakes import sarif as s
from tests.fakes.runner import RUN, client, ok, scan_reply
from tests.unit.application.builders import SCAN_LIMITS, TEST_KEY

VERSION = "1.2.3"


@dataclass(frozen=True)
class Implementation:
    name: str
    factory: Callable[[RunnerClient], ScannerPort]
    valid_output: bytes
    expected_kind: str


IMPLEMENTATIONS = [
    Implementation(
        "semgrep",
        lambda c: semgrep_scanner(c, expected_version=VERSION),
        s.semgrep_sample(),
        "semgrep",
    ),
    Implementation(
        "trivy",
        lambda c: trivy_scanner(c, expected_version=VERSION),
        s.trivy_sample(),
        "trivy",
    ),
]
HOSTILE_OUTPUTS = [
    b"",
    b"not json",
    b"[" * 100_000,
    b'{"version":"2.1.0","runs":[]}',
    s.document([], [s.result("r", uri="../../../etc/shadow")]),
    s.document([], [s.result("r" * 500)]),
    s.document([s.rule("r", score="nan")], [s.result("r")]),
    s.document([], [{"ruleId": "r", "message": {"text": "\x1b[2J<script>"}}]),
]


def scanner(impl: Implementation, data: bytes, **overrides: Any) -> ScannerPort:
    reply = scan_reply(stdout_b64=base64.b64encode(data).decode(), **overrides)
    runner, _ = client(ok(reply))
    return impl.factory(runner)


@pytest.fixture(params=IMPLEMENTATIONS, ids=lambda i: i.name)
def impl(request: pytest.FixtureRequest) -> Implementation:
    return request.param  # type: ignore[no-any-return]


def test_declares_its_kind(impl: Implementation) -> None:
    assert scanner(impl, b"{}").kind.value == impl.expected_kind


def test_valid_output_is_a_complete_scanner_output(impl: Implementation) -> None:
    output = scanner(impl, impl.valid_output).scan(WorkspaceHandle(RUN), SCAN_LIMITS)
    assert isinstance(output, ScannerOutput)
    assert output.kind.value == impl.expected_kind
    assert output.scanner_version == VERSION
    assert output.results
    assert not output.truncated


def test_valid_output_survives_normalization(impl: Implementation) -> None:
    output = scanner(impl, impl.valid_output).scan(WorkspaceHandle(RUN), SCAN_LIMITS)
    result = normalize_output(output, max_findings=100, secret_key=TEST_KEY)
    assert len(result.findings) == len(output.results)
    assert not result.incomplete


@pytest.mark.parametrize("data", HOSTILE_OUTPUTS)
def test_hostile_output_never_escapes_as_a_foreign_exception(
    impl: Implementation, data: bytes
) -> None:
    try:
        output = scanner(impl, data).scan(WorkspaceHandle(RUN), SCAN_LIMITS)
    except PermanentScanError:
        return
    # Si se acepta, la normalización debe contenerlo: nada hostil llega a un Finding.
    result = normalize_output(output, max_findings=100, secret_key=TEST_KEY)
    for finding in result.findings:
        if finding.location is not None:
            assert not finding.location.path.value.startswith("/")
            assert ".." not in finding.location.path.value.split("/")


def test_a_different_scanner_version_is_refused(impl: Implementation) -> None:
    with pytest.raises(PermanentScanError):
        scanner(impl, impl.valid_output, scanner_version="0.0.1").scan(
            WorkspaceHandle(RUN), SCAN_LIMITS
        )


def test_the_request_only_names_workspace_kind_and_limits(impl: Implementation) -> None:
    runner, transport = client(
        ok(scan_reply(stdout_b64=base64.b64encode(impl.valid_output).decode()))
    )
    impl.factory(runner).scan(WorkspaceHandle(RUN), SCAN_LIMITS)
    assert set(json.loads(transport.calls[0][1])) == {
        "step",
        "run_id",
        "scanner_kind",
        "timeout_seconds",
        "max_output_bytes",
    }
