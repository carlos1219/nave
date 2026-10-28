"""Dobles del transporte de GitHub y datos de prueba para la App."""

import json
from collections.abc import Callable, Mapping
from datetime import timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from nave.infrastructure.vcs.app_jwt import AppJwtSigner, AppPrivateKey
from nave.infrastructure.vcs.github_http import GithubApiClient, RawResponse, TransportError
from tests.fakes.memory import FakeClock

TOKEN = "ghs_" + "T" * 36
# Formato nuevo (ADR 0013): ~520 caracteres, con puntos.
LONG_TOKEN = "ghs_12345678_eyJhbGciOiJSUzI1NiJ9." + "A" * 460 + "." + "B" * 43
ISSUER = "Iv1.0123456789abcdef"


def generate_key(bits: int = 2048) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def to_pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


class Call:
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: timedelta,
        max_response_bytes: int,
    ) -> None:
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    @property
    def json(self) -> object:
        return json.loads(self.body) if self.body else None

    @property
    def bearer(self) -> str:
        return self.headers["Authorization"].removeprefix("Bearer ")


Reply = RawResponse | TransportError
Handler = Callable[[Call], Reply]


class FakeGithubTransport:
    """Responde en orden con lo programado, o con un manejador por petición."""

    def __init__(self, *replies: Reply, handler: Handler | None = None) -> None:
        self.replies = list(replies)
        self.handler = handler
        self.calls: list[Call] = []

    def send(  # noqa: PLR0913
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: timedelta,
        max_response_bytes: int,
    ) -> RawResponse:
        call = Call(method, path, dict(headers), body, timeout, max_response_bytes)
        self.calls.append(call)
        reply = self.handler(call) if self.handler else self.replies.pop(0)
        if isinstance(reply, TransportError):
            raise reply
        return reply


def response(
    status: int, payload: object = None, headers: Mapping[str, str] | None = None
) -> RawResponse:
    body = b"" if payload is None else json.dumps(payload).encode()
    return RawResponse(status, body, dict(headers or {}))


def api(
    *replies: Reply, handler: Handler | None = None, clock: FakeClock | None = None
) -> tuple[GithubApiClient, FakeGithubTransport, FakeClock]:
    transport = FakeGithubTransport(*replies, handler=handler)
    the_clock = clock or FakeClock()
    return GithubApiClient(transport=transport, clock=the_clock), transport, the_clock


def signer(clock: FakeClock, key: rsa.RSAPrivateKey | None = None) -> AppJwtSigner:
    return AppJwtSigner(issuer=ISSUER, key=AppPrivateKey(key or _SHARED_KEY), clock=clock)


_SHARED_KEY = generate_key()
