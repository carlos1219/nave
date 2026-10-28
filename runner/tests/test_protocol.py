import json
from pathlib import Path

import pytest

from nave_runner.protocol import (
    AuthenticationError,
    NonceStore,
    NonceStoreFullError,
    compute_signature,
    parse_key,
    verify_request,
)

VECTORS = json.loads(
    (Path(__file__).parents[2] / "contracts" / "runner-protocol-vectors.json").read_text()
)
KEY = parse_key(VECTORS["key_hex"])
CASE = VECTORS["cases"][0]
BODY = CASE["body"].encode()
NOW = float(CASE["timestamp"])


def headers(**overrides: str | None) -> dict[str, str | None]:
    base: dict[str, str | None] = {
        "X-Nave-Timestamp": str(CASE["timestamp"]),
        "X-Nave-Nonce": CASE["nonce"],
        "X-Nave-Signature": CASE["signature"],
    }
    base.update(overrides)
    return base


def verify(
    hdrs: dict[str, str | None] | None = None,
    body: bytes = BODY,
    now: float = NOW,
    nonces: NonceStore | None = None,
) -> None:
    verify_request(
        KEY,
        method="POST",
        path="/v1/runs",
        headers=hdrs or headers(),
        body=body,
        now=now,
        nonces=nonces or NonceStore(ttl_seconds=150, capacity=10),
    )


@pytest.mark.parametrize("case", VECTORS["cases"])
def test_matches_the_shared_vectors(case: dict[str, object]) -> None:
    signature = compute_signature(
        KEY,
        method="POST",
        path="/v1/runs",
        timestamp=int(str(case["timestamp"])),
        nonce=str(case["nonce"]),
        body=str(case["body"]).encode(),
    )
    assert signature == case["signature"]


def test_a_valid_request_passes() -> None:
    verify()


def test_replay_is_rejected() -> None:
    nonces = NonceStore(ttl_seconds=150, capacity=10)
    verify(nonces=nonces)
    with pytest.raises(AuthenticationError):
        verify(nonces=nonces)


def test_a_nonce_may_be_reused_after_it_expired_from_the_store() -> None:
    nonces = NonceStore(ttl_seconds=150, capacity=10)
    assert nonces.register("a" * 32, 1000.0)
    assert not nonces.register("a" * 32, 1100.0)
    assert nonces.register("a" * 32, 1200.0)


@pytest.mark.parametrize("skew", [61, -61, 3600, -86400])
def test_timestamps_outside_the_window_are_rejected(skew: int) -> None:
    with pytest.raises(AuthenticationError):
        verify(now=NOW + skew)


@pytest.mark.parametrize("skew", [0, 60, -60])
def test_timestamps_at_the_edge_are_accepted(skew: int) -> None:
    verify(now=NOW + skew)


def test_tampered_body_is_rejected() -> None:
    with pytest.raises(AuthenticationError):
        verify(body=BODY.replace(b"release", b"scan   "))


def test_wrong_key_is_rejected() -> None:
    with pytest.raises(AuthenticationError):
        verify_request(
            bytes(32),
            method="POST",
            path="/v1/runs",
            headers=headers(),
            body=BODY,
            now=NOW,
            nonces=NonceStore(ttl_seconds=150, capacity=10),
        )


def test_signature_binds_the_path_and_method() -> None:
    for method, path in (("GET", "/v1/runs"), ("POST", "/v1/other")):
        with pytest.raises(AuthenticationError):
            verify_request(
                KEY,
                method=method,
                path=path,
                headers=headers(),
                body=BODY,
                now=NOW,
                nonces=NonceStore(ttl_seconds=150, capacity=10),
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"X-Nave-Timestamp": None},
        {"X-Nave-Nonce": None},
        {"X-Nave-Signature": None},
        {"X-Nave-Timestamp": "abc"},
        {"X-Nave-Timestamp": "-1"},
        {"X-Nave-Timestamp": "1" * 13},
        {"X-Nave-Timestamp": " 1790000000"},
        {"X-Nave-Nonce": "A" * 32},
        {"X-Nave-Nonce": "a" * 31},
        {"X-Nave-Signature": CASE["signature"].upper()},
        {"X-Nave-Signature": CASE["signature"][:-1]},
        {"X-Nave-Signature": CASE["signature"] + "0"},
    ],
)
def test_malformed_headers_are_rejected(overrides: dict[str, str | None]) -> None:
    with pytest.raises(AuthenticationError):
        verify(headers(**overrides))


def test_unauthenticated_requests_cannot_fill_the_nonce_store() -> None:
    nonces = NonceStore(ttl_seconds=150, capacity=2)
    for _ in range(20):
        with pytest.raises(AuthenticationError):
            verify(headers(**{"X-Nave-Signature": "0" * 64}), nonces=nonces)
    verify(nonces=nonces)


def test_a_full_store_refuses_instead_of_forgetting() -> None:
    nonces = NonceStore(ttl_seconds=150, capacity=2)
    assert nonces.register("a" * 32, 1000.0)
    assert nonces.register("b" * 32, 1000.0)
    with pytest.raises(NonceStoreFullError):
        nonces.register("c" * 32, 1000.0)
    assert not nonces.register("a" * 32, 1001.0)


def test_expired_nonces_free_capacity() -> None:
    nonces = NonceStore(ttl_seconds=10, capacity=2)
    nonces.register("a" * 32, 1000.0)
    nonces.register("b" * 32, 1000.0)
    assert nonces.register("c" * 32, 1011.0)


@pytest.mark.parametrize("value", ["", "ab", "A" * 64, "g" * 64, "a" * 63, "a" * 64 + "\n"])
def test_key_format_is_strict(value: str) -> None:
    with pytest.raises(ValueError, match="hex"):
        parse_key(value)


def test_requests_signed_before_the_runner_started_are_rejected() -> None:
    """Un reinicio vacía el almacén de nonces: lo firmado antes no se acepta de nuevo."""
    nonces = NonceStore(ttl_seconds=150, capacity=10, started_at=NOW + 1)
    with pytest.raises(AuthenticationError):
        verify(nonces=nonces)
    verify(nonces=NonceStore(ttl_seconds=150, capacity=10, started_at=NOW))
