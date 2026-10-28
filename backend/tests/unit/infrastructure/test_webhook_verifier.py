import hashlib
import hmac

import pytest

from nave.infrastructure.vcs.webhook_verifier import WebhookVerifier

SECRET = b"s" * 40
OLD = b"o" * 40
BODY = b'{"a":1}'


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_valid_signature() -> None:
    assert WebhookVerifier([SECRET]).verify(BODY, sign(BODY))


def test_rotation_accepts_the_previous_secret_too() -> None:
    verifier = WebhookVerifier([SECRET, OLD])
    assert verifier.verify(BODY, sign(BODY, OLD))
    assert verifier.verify(BODY, sign(BODY, SECRET))
    assert not WebhookVerifier([SECRET]).verify(BODY, sign(BODY, OLD))


def test_signature_is_over_the_raw_bytes() -> None:
    assert not WebhookVerifier([SECRET]).verify(b'{"a": 1}', sign(BODY))
    assert not WebhookVerifier([SECRET]).verify(BODY + b"\n", sign(BODY))


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=",
        "sha1=" + "a" * 40,
        "sha256=" + "A" * 64,  # mayúsculas: GitHub envía minúsculas
        "sha256=" + "a" * 63,
        "sha256=" + "a" * 65,
        "sha256=" + "g" * 64,
        " sha256=" + "a" * 64,
        "sha256=" + "a" * 64 + "\n",
        "SHA256=" + "a" * 64,
    ],
)
def test_malformed_headers_are_rejected(header: str | None) -> None:
    assert not WebhookVerifier([SECRET]).verify(BODY, header)


def test_wrong_signature() -> None:
    assert not WebhookVerifier([SECRET]).verify(BODY, "sha256=" + "0" * 64)


@pytest.mark.parametrize("secrets", [[], [b""], [b"a", b"b", b"c"]])
def test_invalid_secret_sets_are_refused(secrets: list[bytes]) -> None:
    with pytest.raises(ValueError, match="secrets"):
        WebhookVerifier(secrets)


def test_repr_does_not_show_secrets() -> None:
    assert "sss" not in repr(WebhookVerifier([SECRET]))
