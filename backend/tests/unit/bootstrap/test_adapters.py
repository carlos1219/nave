from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from nave.bootstrap.adapters import build_runner_adapters
from nave.bootstrap.config import ConfigurationError, Settings
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.sandbox.runner_fetcher import RunnerRepositoryFetcher
from tests.unit.bootstrap.test_config import RUNNER_ENV, env, read


class Tokens:
    @contextmanager
    def fetch_token(self, *, installation_id: int, repo: RepoRef) -> Iterator[str]:
        yield "ghs_" + "x" * 36


def settings(with_runner: bool) -> Settings:
    overrides = RUNNER_ENV if with_runner else {}
    return Settings.from_environ(env(**overrides), read_secret=read)


def test_builds_the_fetcher_and_one_scanner_per_enabled_tool() -> None:
    fetcher, registry = build_runner_adapters(settings(True), Tokens())
    assert isinstance(fetcher, RunnerRepositoryFetcher)
    assert [s.kind.value for s in registry.scanners()] == ["semgrep", "trivy"]


def test_a_scan_worker_without_a_runner_refuses_to_start() -> None:
    with pytest.raises(ConfigurationError, match="NAVE_RUNNER_URL"):
        build_runner_adapters(settings(False), Tokens())
