import base64
import json
import pickle

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding

from nave.domain.errors import InvalidValueError
from nave.infrastructure.vcs.app_jwt import (
    JWT_BACKDATE_SECONDS,
    JWT_LIFETIME_SECONDS,
    AppJwtSigner,
    AppPrivateKey,
)
from tests.fakes.github import ISSUER, generate_key, to_pem
from tests.fakes.memory import FakeClock

KEY = generate_key()


def decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def make_signer(clock: FakeClock | None = None) -> tuple[AppJwtSigner, FakeClock]:
    the_clock = clock or FakeClock()
    return AppJwtSigner(issuer=ISSUER, key=AppPrivateKey(KEY), clock=the_clock), the_clock


class TestToken:
    def test_has_the_three_unpadded_base64url_segments(self) -> None:
        signer, _ = make_signer()
        token = signer.token()
        assert token.count(".") == 2
        assert "=" not in token
        assert all(part for part in token.split("."))

    def test_header_and_claims(self) -> None:
        signer, clock = make_signer()
        header, claims, _ = signer.token().split(".")
        now = int(clock.now().timestamp())
        assert json.loads(decode(header)) == {"alg": "RS256", "typ": "JWT"}
        assert json.loads(decode(claims)) == {
            "iat": now - JWT_BACKDATE_SECONDS,
            "exp": now + JWT_LIFETIME_SECONDS,
            "iss": ISSUER,
        }

    def test_lives_less_than_the_ten_minutes_github_allows(self) -> None:
        assert JWT_LIFETIME_SECONDS < 10 * 60

    def test_signature_verifies_with_the_public_key(self) -> None:
        signer, _ = make_signer()
        header, claims, signature = signer.token().split(".")
        KEY.public_key().verify(
            decode(signature),
            f"{header}.{claims}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_a_tampered_payload_does_not_verify(self) -> None:
        signer, _ = make_signer()
        header, _, signature = signer.token().split(".")
        forged = base64.urlsafe_b64encode(b'{"iss":"1","iat":1,"exp":99999999999}').rstrip(b"=")
        with pytest.raises(InvalidSignature):
            KEY.public_key().verify(
                decode(signature),
                f"{header}.{forged.decode()}".encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )

    def test_each_token_follows_the_clock(self) -> None:
        signer, clock = make_signer()
        first = signer.token()
        clock.advance(minutes=3)
        assert signer.token() != first

    @pytest.mark.parametrize("issuer", ["", " ", "a b", "x" * 65, "1\n2", "é", "a/b"])
    def test_the_issuer_is_validated(self, issuer: str) -> None:
        with pytest.raises(InvalidValueError):
            AppJwtSigner(issuer=issuer, key=AppPrivateKey(KEY), clock=FakeClock())


class TestPrivateKey:
    def test_loads_an_unencrypted_rsa_pem(self) -> None:
        key = AppPrivateKey.from_pem(to_pem(KEY))
        signer = AppJwtSigner(issuer=ISSUER, key=key, clock=FakeClock())
        assert signer.token().count(".") == 2

    def test_rejects_keys_below_2048_bits(self) -> None:
        with pytest.raises(InvalidValueError):
            AppPrivateKey.from_pem(to_pem(generate_key(1024)))

    def test_rejects_non_rsa_keys(self) -> None:
        pem = (
            ec.generate_private_key(ec.SECP256R1())
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
        with pytest.raises(InvalidValueError):
            AppPrivateKey.from_pem(pem)

    def test_rejects_encrypted_keys(self) -> None:
        pem = KEY.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"pw"),
        ).decode()
        with pytest.raises(InvalidValueError):
            AppPrivateKey.from_pem(pem)

    @pytest.mark.parametrize(
        "pem",
        [
            "",
            "not a key",
            "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----",
            "é" * 5,
            "A" * 20_000,
        ],
    )
    def test_rejects_garbage(self, pem: str) -> None:
        with pytest.raises(InvalidValueError):
            AppPrivateKey.from_pem(pem)

    def test_rejects_a_public_key(self) -> None:
        public = KEY.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        with pytest.raises(InvalidValueError):
            AppPrivateKey.from_pem(public.decode())

    def test_cannot_be_pickled(self) -> None:
        with pytest.raises(TypeError):
            pickle.dumps(AppPrivateKey(KEY))

    def test_the_wrapper_has_no_dict_to_leak_through(self) -> None:
        assert not hasattr(AppPrivateKey(KEY), "__dict__")
