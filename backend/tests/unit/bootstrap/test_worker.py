from pathlib import Path

import pytest

from nave.bootstrap.config import ConfigurationError, Settings
from nave.bootstrap.worker import build_github_adapters, build_worker
from nave.infrastructure.vcs.reporter import GithubVcsReporter
from tests.fakes.github import generate_key, to_pem
from tests.unit.bootstrap.test_config import env


def write(path: Path, text: str) -> str:
    path.write_text(text)
    return str(path)


def test_github_adapters_are_built_from_settings(tmp_path: Path) -> None:
    key = write(tmp_path / "k.pem", to_pem(generate_key()))
    db = write(tmp_path / "db", "pw")
    redis = write(tmp_path / "redis", "pw")
    fp = write(tmp_path / "fp", "ab" * 32)
    settings = Settings.from_environ(
        env(
            NAVE_DATABASE_PASSWORD_FILE=db,
            NAVE_REDIS_PASSWORD_FILE=redis,
            NAVE_FINGERPRINT_KEY_FILE=fp,
            NAVE_GITHUB_APP_ID="42",
            NAVE_GITHUB_APP_PRIVATE_KEY_FILE=key,
        )
    )
    assert settings.github is not None
    tokens, reporter, directory = build_github_adapters(settings.github)
    assert isinstance(reporter, GithubVcsReporter)
    assert directory is not None
    assert "PRIVATE" not in repr(tokens).upper()


def test_worker_refuses_to_start_without_github(tmp_path: Path) -> None:
    db = write(tmp_path / "db", "pw")
    redis = write(tmp_path / "redis", "pw")
    fp = write(tmp_path / "fp", "ab" * 32)
    environ = env(
        NAVE_DATABASE_PASSWORD_FILE=db, NAVE_REDIS_PASSWORD_FILE=redis, NAVE_FINGERPRINT_KEY_FILE=fp
    )
    with pytest.raises(ConfigurationError, match="NAVE_GITHUB_APP_ID"):
        build_worker(environ)
