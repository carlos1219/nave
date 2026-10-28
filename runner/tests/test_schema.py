import json

import pytest

from nave_runner.config import RunnerConfig
from nave_runner.schema import (
    BadRequestError,
    FetchRequest,
    ReleaseRequest,
    ScanRequest,
    parse_request,
)
from tests.conftest import LONG_TOKEN, RUN, TOKEN

FETCH = {
    "step": "fetch",
    "run_id": RUN,
    "owner": "acme",
    "name": "app",
    "commit_sha": "a" * 40,
    "token": TOKEN,
    "max_bytes": 1000,
    "max_files": 100,
    "timeout_seconds": 60,
}
SCAN = {
    "step": "scan",
    "run_id": RUN,
    "scanner_kind": "semgrep",
    "timeout_seconds": 60,
    "max_output_bytes": 1000,
}
RELEASE = {"step": "release", "run_id": RUN}


def body(document: object) -> bytes:
    return json.dumps(document).encode()


def test_valid_requests(config: RunnerConfig) -> None:
    assert isinstance(parse_request(body(FETCH), config), FetchRequest)
    assert isinstance(parse_request(body(SCAN), config), ScanRequest)
    assert parse_request(body(RELEASE), config) == ReleaseRequest(RUN)


@pytest.mark.parametrize("token", [TOKEN, LONG_TOKEN])
def test_both_installation_token_formats_are_accepted(config: RunnerConfig, token: str) -> None:
    request = parse_request(body({**FETCH, "token": token}), config)
    assert isinstance(request, FetchRequest)
    assert request.token == token


def test_the_token_is_not_in_the_repr(config: RunnerConfig) -> None:
    assert TOKEN not in repr(parse_request(body(FETCH), config))


@pytest.mark.parametrize("document", [FETCH, SCAN, RELEASE])
def test_extra_keys_are_rejected(config: RunnerConfig, document: dict[str, object]) -> None:
    for extra in ("image", "network", "privileged", "command", "mounts", "env", "volume"):
        with pytest.raises(BadRequestError):
            parse_request(body({**document, extra: "x"}), config)


@pytest.mark.parametrize("document", [FETCH, SCAN, RELEASE])
def test_missing_keys_are_rejected(config: RunnerConfig, document: dict[str, object]) -> None:
    for key in list(document):
        with pytest.raises(BadRequestError):
            parse_request(body({k: v for k, v in document.items() if k != key}), config)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"null",
        b"[]",
        b'"x"',
        b"{",
        b'{"step":"fetch","step":"release","run_id":"' + RUN.encode() + b'"}',
        b'{"step":"unknown"}',
        b'{"step":5}',
        b"{}",
        '{"step":"release","run_id":"é"}'.encode(),
        b"[" * 100_000,
        b'{"step":"release","run_id":"' + RUN.encode() + b'","x":NaN}',
    ],
)
def test_garbage_is_rejected(config: RunnerConfig, raw: bytes) -> None:
    with pytest.raises(BadRequestError):
        parse_request(raw, config)


@pytest.mark.parametrize(
    "run_id",
    ["", "../etc", RUN.upper(), RUN[:-1], RUN + "0", RUN[:-1] + "\n", "a/b" + "0" * 29, 5, None],
)
def test_run_id_must_be_32_lowercase_hex(config: RunnerConfig, run_id: object) -> None:
    for base in (FETCH, SCAN, RELEASE):
        with pytest.raises(BadRequestError):
            parse_request(body({**base, "run_id": run_id}), config)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("owner", ""),
        ("owner", "-bad"),
        ("owner", "bad-"),
        ("owner", "a" * 40),
        ("owner", "ac me"),
        ("owner", "--upload-pack=x"),
        ("name", ""),
        ("name", "."),
        ("name", ".."),
        ("name", "a/b"),
        ("name", "a" * 101),
        ("name", "x\n"),
        ("name", "app.git"),
        ("name", "a;rm"),
        ("commit_sha", "a" * 39),
        ("commit_sha", "A" * 40),
        ("commit_sha", "main"),
        ("commit_sha", "a" * 40 + "\n"),
        ("token", ""),
        ("token", "short"),
        ("token", "ghs_" + "x" * 1030),
        ("token", LONG_TOKEN + "\n"),
        ("token", TOKEN + "\n"),
        ("token", "a b" * 10),
        ("max_bytes", 0),
        ("max_bytes", -1),
        ("max_bytes", (1 << 30) + 1),
        ("max_bytes", True),
        ("max_bytes", 1.5),
        ("max_bytes", "10"),
        ("max_files", 200_001),
        ("max_files", 0),
        ("timeout_seconds", 3601),
        ("timeout_seconds", 0),
        ("timeout_seconds", None),
    ],
)
def test_fetch_fields_are_validated(config: RunnerConfig, key: str, value: object) -> None:
    with pytest.raises(BadRequestError):
        parse_request(body({**FETCH, key: value}), config)


@pytest.mark.parametrize("name", ["-x", "-", "--x"])
def test_names_starting_with_a_dash_are_valid_github_names(config: RunnerConfig, name: str) -> None:
    # La plantilla los pasa como `--name=<valor>`: nunca pueden ser una opción (ver
    # test_sandbox_spec).
    request = parse_request(body({**FETCH, "name": name}), config)
    assert isinstance(request, FetchRequest)
    assert request.name == name


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("scanner_kind", "bandit"),
        ("scanner_kind", "SEMGREP"),
        ("scanner_kind", ""),
        ("scanner_kind", "semgrep "),
        ("scanner_kind", "../semgrep"),
        ("timeout_seconds", 7201),
        ("timeout_seconds", 0),
        ("max_output_bytes", (64 << 20) + 1),
        ("max_output_bytes", 0),
    ],
)
def test_scan_fields_are_validated(config: RunnerConfig, key: str, value: object) -> None:
    with pytest.raises(BadRequestError):
        parse_request(body({**SCAN, key: value}), config)


def test_limits_cannot_exceed_the_runner_caps(config: RunnerConfig) -> None:
    parse_request(body({**FETCH, "max_bytes": config.max_fetch_bytes}), config)
    with pytest.raises(BadRequestError):
        parse_request(body({**FETCH, "max_bytes": config.max_fetch_bytes + 1}), config)
