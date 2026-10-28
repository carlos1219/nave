"""Adaptador genérico de escáner que se ejecuta en el `scan-runner` y emite SARIF.

Cada escáner concreto (Semgrep, Trivy) aporta solo lo que le es propio: el `kind`, la
versión esperada y la función que traduce un resultado SARIF a `NormalizedResult`. El
comando, la imagen y la configuración los fija el runner con plantillas propias (AM-402,
AM-503): este adaptador nunca los transmite.
"""

import hashlib
from collections.abc import Callable
from typing import Final

from nave.application.dto.scanning import NormalizedResult, ScanLimits, ScannerOutput
from nave.application.dto.workspace import WorkspaceHandle
from nave.application.errors import ScannerExecutionError, ScannerOutputInvalidError
from nave.domain.errors import InvalidValueError
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.infrastructure.sandbox.runner_client import RunnerClient
from nave.infrastructure.scanners.sarif import SarifResult, parse_sarif

ResultMapper = Callable[[SarifResult], NormalizedResult | None]
IncompleteMarker = Callable[[SarifResult], bool]

_SUCCESS_EXIT_CODE: Final = 0


class RunnerScanner:
    def __init__(
        self,
        *,
        kind: ScannerKind,
        expected_version: str,
        client: RunnerClient,
        mapper: ResultMapper,
        incomplete_when: IncompleteMarker | None = None,
    ) -> None:
        self._kind = kind
        self._expected_version = expected_version
        self._client = client
        self._mapper = mapper
        self._incomplete_when = incomplete_when

    @property
    def kind(self) -> ScannerKind:
        return self._kind

    def scan(self, workspace: WorkspaceHandle, limits: ScanLimits) -> ScannerOutput:
        reply = self._client.scan(run_id=workspace.value, kind=self._kind, limits=limits)
        if reply.scanner_version != self._expected_version:
            # Una versión distinta de la fijada cambia las reglas y los formatos sin aviso.
            raise ScannerExecutionError("scanner_version_mismatch")
        if reply.exit_code != _SUCCESS_EXIT_CODE:
            raise ScannerExecutionError("scanner_exit_code")
        parsed = parse_sarif(reply.stdout)
        mapped: list[NormalizedResult] = []
        dropped = parsed.dropped
        for sarif_result in parsed.results:
            result = self._mapper(sarif_result)
            if result is None:
                dropped += 1
            else:
                mapped.append(result)
        over_limit = len(mapped) > limits.max_results
        # El escáner avisa de que no analizó parte del código (p. ej. ficheros demasiado
        # grandes): la ausencia de hallazgos allí no prueba nada.
        skipped = self._incomplete_when is not None and any(
            self._incomplete_when(r) for r in parsed.results
        )
        try:
            return ScannerOutput(
                kind=self._kind,
                scanner_version=reply.scanner_version,
                image_digest=reply.image_digest,
                output_sha256=hashlib.sha256(reply.stdout).hexdigest(),
                results=tuple(mapped[: limits.max_results]),
                # Descartar resultados equivale a perderlos: la salida es incompleta y
                # un PASS no es fiable (ver NormalizationResult.incomplete).
                truncated=over_limit or dropped > 0 or skipped,
            )
        except InvalidValueError as error:
            raise ScannerOutputInvalidError("invalid_scanner_metadata") from error
