"""Punto de entrada del servicio `worker` (ADR 0008, ADR 0014).

`celery --app nave.bootstrap.worker:worker_app worker -Q <cola>`: cada contenedor sirve
una sola cola (`scan`, `report` o `maintenance`) con el modelo prefork. Aquí, y solo aquí,
se juntan la configuración, las credenciales de GitHub y el grafo de objetos.
"""

import os
from collections.abc import Mapping

from celery import Celery

from nave.bootstrap.adapters import build_runner_adapters
from nave.bootstrap.config import ConfigurationError, GithubSettings, Settings
from nave.bootstrap.container import Container, ExternalAdapters, build_container
from nave.infrastructure.observability.logging import configure_logging
from nave.infrastructure.system import SystemClock
from nave.infrastructure.vcs.app_identity import GithubAppIdentityProvider
from nave.infrastructure.vcs.app_jwt import AppJwtSigner
from nave.infrastructure.vcs.directory import GithubInstallationDirectory
from nave.infrastructure.vcs.github_http import GithubApiClient, HttpsGithubTransport
from nave.infrastructure.vcs.installation_tokens import GithubInstallationTokens
from nave.infrastructure.vcs.reporter import GithubVcsReporter


def build_github_adapters(
    github: GithubSettings,
) -> tuple[GithubInstallationTokens, GithubVcsReporter, GithubInstallationDirectory]:
    clock = SystemClock()
    api = GithubApiClient(
        transport=HttpsGithubTransport(base_url=github.api_url, proxy_url=github.egress_proxy_url),
        clock=clock,
    )
    signer = AppJwtSigner(issuer=str(github.app_id), key=github.private_key, clock=clock)
    tokens = GithubInstallationTokens(
        api=api, signer=signer, clock=clock, sarif_upload=github.sarif_upload
    )
    reporter = GithubVcsReporter(
        api=api,
        tokens=tokens,
        identity=GithubAppIdentityProvider(api=api, signer=signer),
        sarif_upload=github.sarif_upload,
    )
    directory = GithubInstallationDirectory(api=api, signer=signer, tokens=tokens)
    return tokens, reporter, directory


def build_worker(environ: Mapping[str, str]) -> Container:
    settings = Settings.from_environ(environ)
    if settings.github is None:
        raise ConfigurationError("NAVE_GITHUB_APP_ID is required for the worker")
    tokens, reporter, directory = build_github_adapters(settings.github)
    fetcher, registry = build_runner_adapters(settings, tokens)
    return build_container(settings, ExternalAdapters(fetcher, registry, reporter, directory))


def worker_app() -> Celery:
    configure_logging("nave-worker")
    return build_worker(os.environ).celery
