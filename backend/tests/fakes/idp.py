"""IdP OIDC falso: documento de descubrimiento, JWKS, endpoint de token y firma real de JWT."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs

from joserfc import jwt
from joserfc.jwk import RSAKey

from nave.infrastructure.auth.oidc import OidcResponse, OidcTransportError
from tests.fakes.memory import FakeClock

ISSUER = "https://idp.example.com/realms/nave"
CLIENT_ID = "nave"
CLIENT_SECRET = "s3cret/with space+plus"
REDIRECT_URI = "https://nave.test/api/v1/auth/callback"


def new_key(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig"})


@dataclass
class Requests:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def count(self, suffix: str) -> int:
        return sum(1 for c in self.calls if c["url"].endswith(suffix))


class FakeIdp:
    """Se comporta como el IdP; cada test puede romper una parte concreta."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.key = new_key("k1")
        self.published: list[RSAKey] = [self.key]
        self.nonce = "the-nonce"
        self.metadata_overrides: dict[str, Any] = {}
        self.claim_overrides: dict[str, Any] = {}
        self.drop_claims: set[str] = set()
        self.signing_key: RSAKey = self.key
        self.header: dict[str, Any] = {"alg": "RS256", "kid": "k1"}
        self.token_status = 200
        self.token_body_override: bytes | None = None
        self.down = False
        self.id_token_override: str | None = None
        self.requests = Requests()

    # -- construcción de tokens ------------------------------------------------------

    def claims(self) -> dict[str, Any]:
        now = int(self.clock.now().timestamp())
        base: dict[str, Any] = {
            "iss": ISSUER,
            "sub": "alice-sub",
            "aud": CLIENT_ID,
            "exp": now + 300,
            "iat": now,
            "nonce": self.nonce,
            "name": "Alice Example",
            "email": "alice@example.com",
            "email_verified": True,
        }
        base.update(self.claim_overrides)
        for name in self.drop_claims:
            base.pop(name, None)
        return base

    def id_token(self) -> str:
        if self.id_token_override is not None:
            return self.id_token_override
        return jwt.encode(self.header, self.claims(), self.signing_key)

    def rotate(self) -> None:
        """Publica una clave nueva y firma con ella."""
        self.key = new_key("k2")
        self.published = [self.key]
        self.signing_key = self.key
        self.header = {"alg": "RS256", "kid": "k2"}

    # -- transporte ------------------------------------------------------------------

    def metadata(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
            "code_challenge_methods_supported": ["S256", "plain"],
        }
        document.update(self.metadata_overrides)
        return document

    def send(  # noqa: PLR0913
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: timedelta,
        max_bytes: int,
    ) -> OidcResponse:
        self.requests.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        if self.down:
            raise OidcTransportError("down")
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return OidcResponse(200, json.dumps(self.metadata()).encode())
        if url.endswith("/certs"):
            keys = [k.as_dict(private=False) for k in self.published]
            return OidcResponse(200, json.dumps({"keys": keys}).encode())
        if url.endswith("/token"):
            if self.token_body_override is not None:
                return OidcResponse(self.token_status, self.token_body_override)
            payload = {
                "id_token": self.id_token(),
                "access_token": "opaque",
                "token_type": "Bearer",
            }
            return OidcResponse(self.token_status, json.dumps(payload).encode())
        return OidcResponse(404, b"{}")

    def form(self) -> dict[str, str]:
        """Última petición al endpoint de token, decodificada."""
        call = next(c for c in reversed(self.requests.calls) if c["url"].endswith("/token"))
        return {k: v[0] for k, v in parse_qs(call["body"].decode()).items()}
