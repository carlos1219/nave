from pathlib import Path

import pytest

from nave_runner.config import ConfigurationError, load_config
from tests.conftest import DIGEST

KEY_HEX = "ab" * 32
IMAGE = f"registry.internal/img@{DIGEST}"


def read(path: Path) -> str:
    return KEY_HEX


def env(**overrides: str | None) -> dict[str, str]:
    base: dict[str, str | None] = {
        "NAVE_RUNNER_HMAC_KEY_FILE": "/run/secrets/runner_hmac_key",
        "NAVE_RUNNER_FETCH_IMAGE": IMAGE,
        "NAVE_RUNNER_SCANNERS": "semgrep,trivy",
        "NAVE_RUNNER_IMAGE_SEMGREP": IMAGE,
        "NAVE_RUNNER_VERSION_SEMGREP": "1.180.0",
        "NAVE_RUNNER_IMAGE_TRIVY": IMAGE,
        "NAVE_RUNNER_VERSION_TRIVY": "0.58.0",
        "NAVE_RUNNER_EGRESS_PROXY_URL": "http://egress-proxy:3128",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def test_defaults_are_complete() -> None:
    config = load_config(env(), read)
    assert config.key == bytes.fromhex(KEY_HEX)
    assert set(config.scanners) == {"semgrep", "trivy"}
    assert config.scanners["semgrep"].digest == DIGEST
    assert config.runtime is None
    assert config.max_concurrent == 4
    assert config.orphan_max_age_seconds >= config.max_fetch_seconds + config.max_scan_seconds


def test_repr_hides_the_key() -> None:
    assert KEY_HEX[:8] not in repr(load_config(env(), read))


@pytest.mark.parametrize(
    "overrides",
    [
        {"NAVE_RUNNER_HMAC_KEY_FILE": None},
        {"NAVE_RUNNER_FETCH_IMAGE": None},
        {"NAVE_RUNNER_FETCH_IMAGE": "registry/img:latest"},
        {"NAVE_RUNNER_FETCH_IMAGE": "registry/img@sha256:abc"},
        {"NAVE_RUNNER_IMAGE_SEMGREP": "semgrep/semgrep:1.180.0"},
        {"NAVE_RUNNER_IMAGE_TRIVY": f"UPPER/img@{DIGEST}"},
        {"NAVE_RUNNER_SCANNERS": None},
        {"NAVE_RUNNER_SCANNERS": ""},
        {"NAVE_RUNNER_SCANNERS": "semgrep,semgrep"},
        {"NAVE_RUNNER_SCANNERS": "semgrep,Bad Name"},
        {"NAVE_RUNNER_SCANNERS": "semgrep,gitleaks"},
        {"NAVE_RUNNER_VERSION_SEMGREP": None},
        {"NAVE_RUNNER_VERSION_SEMGREP": "1.0 beta"},
        {"NAVE_RUNNER_EGRESS_PROXY_URL": None},
        {"NAVE_RUNNER_EGRESS_PROXY_URL": "https://egress-proxy:3128"},
        {"NAVE_RUNNER_EGRESS_PROXY_URL": "http://u:p@egress-proxy:3128"},
        {"NAVE_RUNNER_RUNTIME": "runsc --privileged"},
        {"NAVE_RUNNER_ENGINE_BINARY": "docker"},
        {"NAVE_RUNNER_ENGINE_BINARY": "/bin/sh -c x"},
        {"NAVE_RUNNER_MAX_CONCURRENT": "0"},
        {"NAVE_RUNNER_MAX_CONCURRENT": "ten"},
        {"NAVE_RUNNER_MAX_CONCURRENT": "-1"},
        {"NAVE_RUNNER_MAX_FETCH_BYTES": str(2 << 30)},
        {"NAVE_RUNNER_MAX_FETCH_SECONDS": "7200"},
        {"NAVE_RUNNER_LISTEN_PORT": "70000"},
        {"NAVE_RUNNER_HMAC_KEY": KEY_HEX},
        {"NAVE_RUNNER_TYPO": "1"},
    ],
)
def test_unsafe_or_incomplete_settings_are_refused(overrides: dict[str, str | None]) -> None:
    with pytest.raises(ConfigurationError):
        load_config(env(**overrides), read)


def test_a_malformed_key_is_refused_without_echoing_it() -> None:
    with pytest.raises(ConfigurationError) as raised:
        load_config(env(), lambda _: "not-a-key-but-secret")
    assert "secret" not in str(raised.value)


def test_unrelated_variables_are_ignored() -> None:
    load_config(env(PATH="/usr/bin", HOME="/root"), read)


def test_optional_settings() -> None:
    config = load_config(
        env(
            NAVE_RUNNER_RUNTIME="runsc",
            NAVE_RUNNER_SECCOMP_PROFILE="/etc/nave/seccomp.json",
            NAVE_RUNNER_MAX_CONCURRENT="8",
            NAVE_RUNNER_EGRESS_NETWORK="custom-egress",
        ),
        read,
    )
    assert (config.runtime, config.max_concurrent, config.egress_network) == (
        "runsc",
        8,
        "custom-egress",
    )
    assert config.seccomp_profile == "/etc/nave/seccomp.json"
