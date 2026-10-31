"""Cliente OIDC contra un IdP falso con firmas reales (ADR 0004, ADR 0016, AM-103)."""

import base64
import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from joserfc import jwt
from joserfc.jwk import OctKey

from nave.application.errors import AuthenticationError, IdentityProviderUnavailableError
from nave.infrastructure.auth import oidc as oidc_module
from nave.infrastructure.auth.oidc import OidcClient, OidcClientConfig
from tests.fakes.idp import (
    CLIENT_ID,
    CLIENT_SECRET,
    ISSUER,
    REDIRECT_URI,
    FakeIdp,
    new_key,
)
from tests.fakes.memory import FakeClock


class Setup:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.idp = FakeIdp(self.clock)
        self.client = OidcClient(
            OidcClientConfig(
                issuer=ISSUER,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
            ),
            self.idp,
            self.clock,
        )

    def complete(self, nonce: str = "the-nonce") -> Any:
        return self.client.complete(code="the-code", code_verifier="v" * 43, nonce=nonce)


@pytest.fixture
def s() -> Setup:
    return Setup()


class TestAuthorizationUrl:
    def test_carries_pkce_state_nonce_and_exact_redirect(self, s: Setup) -> None:
        url = s.client.authorization_url(state="st", nonce="no", code_challenge="ch")
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
            f"{ISSUER}/protocol/openid-connect/auth"
        )
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert query == {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid profile email",
            "state": "st",
            "nonce": "no",
            "code_challenge": "ch",
            "code_challenge_method": "S256",
        }

    def test_discovery_is_cached(self, s: Setup) -> None:
        for _ in range(3):
            s.client.authorization_url(state="a", nonce="b", code_challenge="c")
        assert s.idp.requests.count("openid-configuration") == 1

    def test_discovery_is_refreshed_after_its_ttl_and_stale_data_survives_an_outage(
        self, s: Setup
    ) -> None:
        s.client.authorization_url(state="a", nonce="b", code_challenge="c")
        s.clock.advance(hours=2)
        s.idp.down = True
        s.client.authorization_url(state="a", nonce="b", code_challenge="c")  # copia vieja
        s.clock.advance(hours=25)
        with pytest.raises(IdentityProviderUnavailableError):
            s.client.authorization_url(state="a", nonce="b", code_challenge="c")


class TestDiscoveryIsNotTrusted:
    @pytest.mark.parametrize(
        "override",
        [
            {"issuer": "https://evil.example.com/realms/nave"},
            {"issuer": ISSUER + "/"},
            {"token_endpoint": "https://evil.example.com/token"},
            {"jwks_uri": "https://evil.example.com/certs"},
            {"authorization_endpoint": "https://evil.example.com/auth"},
            {"token_endpoint": "http://idp.example.com/token"},
            {"token_endpoint": "https://idp.example.com:8443/token"},
            {"token_endpoint": "https://user:pw@idp.example.com/token"},
            {"token_endpoint": None},
            {"token_endpoint": 5},
            {"authorization_endpoint": "https://idp.example.com/auth?prompt=none"},
            {"code_challenge_methods_supported": ["plain"]},
        ],
    )
    def test_a_document_that_leaves_the_issuer_origin_is_refused(
        self, s: Setup, override: dict[str, Any]
    ) -> None:
        s.idp.metadata_overrides = override
        with pytest.raises(IdentityProviderUnavailableError):
            s.client.authorization_url(state="a", nonce="b", code_challenge="c")
        # Y el secreto del cliente nunca llegó a enviarse a otro sitio.
        assert not [c for c in s.idp.requests.calls if c["method"] == "POST"]

    @pytest.mark.parametrize("body", [b"[]", b"nope", b'{"issuer": NaN}'])
    def test_garbage_documents_are_unavailability(self, s: Setup, body: bytes) -> None:
        original = s.idp.send

        def send(**kwargs: Any) -> Any:
            if kwargs["url"].endswith("openid-configuration"):
                return oidc_module.OidcResponse(200, body)
            return original(**kwargs)

        s.client._transport = type("T", (), {"send": staticmethod(send)})()
        with pytest.raises(IdentityProviderUnavailableError):
            s.client.authorization_url(state="a", nonce="b", code_challenge="c")


class TestTokenExchange:
    def test_valid_flow_returns_the_identity(self, s: Setup) -> None:
        identity = s.complete()
        assert identity.issuer == ISSUER
        assert identity.subject == "alice-sub"
        assert identity.display_name == "Alice Example"
        assert identity.email == "alice@example.com"

    def test_request_uses_basic_auth_and_pkce_and_never_puts_the_secret_in_the_body(
        self, s: Setup
    ) -> None:
        s.complete()
        call = next(c for c in s.idp.requests.calls if c["url"].endswith("/token"))
        scheme, _, encoded = call["headers"]["Authorization"].partition(" ")
        assert scheme == "Basic"
        user, _, password = base64.b64decode(encoded).decode().partition(":")
        assert user == CLIENT_ID
        assert password == "s3cret%2Fwith%20space%2Bplus"  # codificación de RFC 6749 §2.3.1
        assert CLIENT_SECRET not in call["body"].decode()
        assert s.idp.form() == {
            "grant_type": "authorization_code",
            "code": "the-code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": "v" * 43,
        }

    def test_a_400_from_the_idp_is_a_failed_login_not_an_outage(self, s: Setup) -> None:
        s.idp.token_status = 400
        s.idp.token_body_override = b'{"error":"invalid_grant"}'
        with pytest.raises(AuthenticationError):
            s.complete()

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_other_failures_are_an_outage(self, s: Setup, status: int) -> None:
        s.idp.token_status = status
        s.idp.token_body_override = b"{}"
        with pytest.raises(IdentityProviderUnavailableError):
            s.complete()

    @pytest.mark.parametrize("body", [b"nope", b"[]", b'{"id_token": NaN}'])
    def test_unusable_token_responses_are_an_outage(self, s: Setup, body: bytes) -> None:
        s.idp.token_body_override = body
        with pytest.raises(IdentityProviderUnavailableError):
            s.complete()

    @pytest.mark.parametrize("body", [b"{}", b'{"id_token": 5}', b'{"id_token": ""}'])
    def test_a_response_without_an_id_token_fails_the_login(self, s: Setup, body: bytes) -> None:
        s.idp.token_body_override = body
        with pytest.raises(AuthenticationError):
            s.complete()

    def test_transport_errors_are_an_outage(self, s: Setup) -> None:
        s.client.authorization_url(state="a", nonce="b", code_challenge="c")
        s.idp.down = True
        with pytest.raises(IdentityProviderUnavailableError):
            s.complete()


class TestIdTokenValidation:
    def test_wrong_nonce(self, s: Setup) -> None:
        with pytest.raises(AuthenticationError):
            s.complete(nonce="another")

    @pytest.mark.parametrize(
        "override",
        [
            {"iss": "https://evil.example.com"},
            {"aud": "someone-else"},
            {"aud": ["nave", "other"]},  # sin azp
            {"aud": ["nave", "other"], "azp": "other"},
            {"sub": ""},
            {"sub": 5},
            {"exp": 1},
            {"iat": 4_000_000_000},
            {"nonce": "x"},
        ],
    )
    def test_bad_claims_fail_the_login(self, s: Setup, override: dict[str, Any]) -> None:
        s.idp.claim_overrides = override
        with pytest.raises(AuthenticationError):
            s.complete()

    @pytest.mark.parametrize("claim", ["iss", "aud", "sub", "exp", "iat", "nonce"])
    def test_missing_essential_claims_fail_the_login(self, s: Setup, claim: str) -> None:
        s.idp.drop_claims = {claim}
        with pytest.raises(AuthenticationError):
            s.complete()

    def test_audience_list_with_matching_azp_is_accepted(self, s: Setup) -> None:
        s.idp.claim_overrides = {"aud": ["nave", "other"], "azp": "nave"}
        assert s.complete().subject == "alice-sub"

    def test_clock_skew_within_the_leeway_is_tolerated_but_not_beyond(self, s: Setup) -> None:
        s.idp.claim_overrides = {"exp": int(s.clock.now().timestamp()) - 30}
        assert s.complete().subject == "alice-sub"
        s.idp.claim_overrides = {"exp": int(s.clock.now().timestamp()) - 120}
        with pytest.raises(AuthenticationError):
            s.complete()

    def test_signature_from_a_key_the_idp_does_not_publish(self, s: Setup) -> None:
        s.idp.signing_key = new_key("k1")  # mismo kid, otra clave
        with pytest.raises(AuthenticationError):
            s.complete()

    def test_unsigned_tokens_are_refused(self, s: Setup) -> None:
        def b64(value: object) -> str:
            raw = json.dumps(value).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        s.idp.id_token_override = f"{b64({'alg': 'none'})}.{b64(s.idp.claims())}."
        with pytest.raises(AuthenticationError):
            s.complete()

    def test_symmetric_algorithms_are_refused(self, s: Setup) -> None:
        # Confusión de algoritmos: HS256 usando algo público como secreto.
        secret = OidcClientConfig.__name__ * 3
        s.idp.id_token_override = jwt.encode(
            {"alg": "HS256", "kid": "k1"}, s.idp.claims(), OctKey.import_key(secret)
        )
        with pytest.raises(AuthenticationError):
            s.complete()

    def test_garbage_is_refused(self, s: Setup) -> None:
        for garbage in ("x", "a.b.c", "....", "é" * 10):
            s.idp.id_token_override = garbage
            with pytest.raises(AuthenticationError):
                s.complete()

    def test_a_rotated_key_is_picked_up_with_one_refresh(self, s: Setup) -> None:
        s.complete()
        s.clock.advance(minutes=5)
        s.idp.rotate()
        assert s.complete().subject == "alice-sub"
        assert s.idp.requests.count("/certs") == 2

    def test_unknown_keys_cannot_be_used_to_hammer_the_idp(self, s: Setup) -> None:
        s.idp.signing_key = new_key("unknown")
        s.idp.header = {"alg": "RS256", "kid": "unknown"}
        for _ in range(5):
            with pytest.raises(AuthenticationError):
                s.complete()
        assert s.idp.requests.count("/certs") == 1  # el intervalo mínimo lo impide

    def test_jwks_is_cached_between_logins(self, s: Setup) -> None:
        for _ in range(3):
            s.complete()
        assert s.idp.requests.count("/certs") == 1

    def test_private_key_material_in_the_jwks_is_ignored(self, s: Setup) -> None:
        s.idp.published = [s.idp.key]
        original = s.idp.send

        def send(**kwargs: Any) -> Any:
            response = original(**kwargs)
            if kwargs["url"].endswith("/certs"):
                keys = [{**s.idp.key.as_dict(private=True)}, {"kty": "oct", "k": "AAAA"}]
                return oidc_module.OidcResponse(200, json.dumps({"keys": keys}).encode())
            return response

        s.client._transport = type("T", (), {"send": staticmethod(send)})()
        with pytest.raises(IdentityProviderUnavailableError):
            s.complete()


class TestIdentityMapping:
    def test_unverified_email_is_dropped(self, s: Setup) -> None:
        s.idp.claim_overrides = {"email_verified": False}
        assert s.complete().email is None
        s.idp.claim_overrides = {"email_verified": "true"}  # cadena, no booleano
        assert s.complete().email is None

    def test_weird_profile_data_never_blocks_the_login(self, s: Setup) -> None:
        bidi, escape = chr(0x202E), chr(0x1B)
        s.idp.claim_overrides = {"name": f"{escape}[31mAlice{bidi}", "email": "not an address"}
        identity = s.complete()
        assert identity.subject == "alice-sub"
        assert escape not in (identity.display_name or "")
        assert bidi not in (identity.display_name or "")
        assert identity.email is None

    def test_long_names_are_truncated(self, s: Setup) -> None:
        s.idp.claim_overrides = {"name": "n" * 1000}
        assert len(s.complete().display_name or "") <= 255

    def test_falls_back_to_preferred_username(self, s: Setup) -> None:
        s.idp.drop_claims = {"name"}
        s.idp.claim_overrides = {"preferred_username": "alice"}
        assert s.complete().display_name == "alice"

    def test_unusable_subject_fails_the_login(self, s: Setup) -> None:
        s.idp.claim_overrides = {"sub": "x" * 300}
        with pytest.raises(AuthenticationError):
            s.complete()


def test_repr_of_the_config_hides_the_secret() -> None:
    config = OidcClientConfig(ISSUER, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
    assert CLIENT_SECRET not in repr(config)


def test_timeouts_are_bounded() -> None:
    assert timedelta(seconds=30) >= oidc_module.TIMEOUT
