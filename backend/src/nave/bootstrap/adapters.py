"""Adaptadores de escaneo del worker: cliente del `scan-runner`, fetcher y registro.

El token de instalación lo aporta la integración con GitHub (fase 4); aquí solo se
cablea. Es la única pieza de `bootstrap` que decide qué escáneres están habilitados.
"""

from nave.application.ports.scanning import RepositoryFetcherPort
from nave.bootstrap.config import ConfigurationError, Settings
from nave.infrastructure.sandbox.runner_client import HttpTransport, RunnerClient
from nave.infrastructure.sandbox.runner_fetcher import (
    InstallationTokenProvider,
    RunnerRepositoryFetcher,
)
from nave.infrastructure.scanners.registry import ScannerRegistry
from nave.infrastructure.scanners.semgrep import semgrep_scanner
from nave.infrastructure.scanners.trivy import trivy_scanner
from nave.infrastructure.system import SystemClock


def build_runner_adapters(
    settings: Settings, tokens: InstallationTokenProvider
) -> tuple[RepositoryFetcherPort, ScannerRegistry]:
    runner = settings.runner
    if runner is None:
        raise ConfigurationError("NAVE_RUNNER_URL is required for the scan worker")
    client = RunnerClient(transport=HttpTransport(runner.url), key=runner.key, clock=SystemClock())
    registry = ScannerRegistry(
        [
            semgrep_scanner(client, expected_version=runner.semgrep_version),
            trivy_scanner(client, expected_version=runner.trivy_version),
        ]
    )
    return RunnerRepositoryFetcher(client=client, tokens=tokens), registry
