"""Transporte HTTP del cliente OIDC: solo el origen del emisor, sin redirecciones."""

import http.client
from datetime import timedelta
from typing import Any

import pytest

from nave.infrastructure.auth.oidc import (
    HttpOidcTransport,
    OidcClientConfig,
    OidcTransportError,
    origin_of,
)

SEND: dict[str, Any] = {
    "method": "GET",
    "headers": {},
    "body": None,
    "timeout": timedelta(seconds=5),
    "max_bytes": 100,
}


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, amount: int) -> bytes:
        return self._body[:amount]


class FakeConnection:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.requests: list[tuple[str, str]] = []
        self.closed = False
        self.tunnel: tuple[str, int] | None = None

    def set_tunnel(self, host: str, port: int) -> None:
        self.tunnel = (host, port)

    def request(self, method: str, target: str, **_: Any) -> None:
        self.requests.append((method, target))

    def getresponse(self) -> FakeResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True


def transport(connection: FakeConnection, **kwargs: Any) -> tuple[HttpOidcTransport, list[tuple]]:
    made: list[tuple] = []

    def factory(scheme: str, host: str, port: int, timeout: float) -> Any:
        made.append((scheme, host, port, timeout))
        return connection

    return (
        HttpOidcTransport(
            origin_url="https://idp.example.com", connection_factory=factory, **kwargs
        ),
        made,
    )


def test_connects_to_the_issuer_origin_and_closes() -> None:
    connection = FakeConnection(FakeResponse(200, b"{}"))
    t, made = transport(connection)
    response = t.send(url="https://idp.example.com/a/b?x=1", **SEND)
    assert (response.status, response.body) == (200, b"{}")
    assert made == [("https", "idp.example.com", 443, 5.0)]
    assert connection.requests == [("GET", "/a/b?x=1")]
    assert connection.closed


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/a",
        "https://idp.example.com:8443/a",
        "http://idp.example.com/a",
        "https://user@idp.example.com/a",
        "https://idp.example.com/a#frag",
        "ftp://idp.example.com/a",
        "//idp.example.com/a",
        "https://idp.example.com.evil.example/a",
    ],
)
def test_other_origins_are_refused_before_connecting(url: str) -> None:
    connection = FakeConnection(FakeResponse(200, b"{}"))
    t, made = transport(connection)
    with pytest.raises(OidcTransportError):
        t.send(url=url, **SEND)
    assert made == []


def test_redirects_are_not_followed_they_are_just_a_non_200() -> None:
    t, _ = transport(FakeConnection(FakeResponse(302, b"")))
    assert t.send(url="https://idp.example.com/a", **SEND).status == 302


def test_oversized_responses_are_refused() -> None:
    t, _ = transport(FakeConnection(FakeResponse(200, b"x" * 500)))
    with pytest.raises(OidcTransportError, match="too large"):
        t.send(url="https://idp.example.com/a", **SEND)


@pytest.mark.parametrize("error", [OSError("boom"), http.client.HTTPException("bad")])
def test_network_errors_become_transport_errors_without_detail(error: Exception) -> None:
    connection = FakeConnection(error)
    t, _ = transport(connection)
    with pytest.raises(OidcTransportError) as raised:
        t.send(url="https://idp.example.com/a", **SEND)
    assert "boom" not in str(raised.value)
    assert connection.closed


class TestOrigins:
    def test_plain_http_only_for_loopback_with_the_switch(self) -> None:
        assert origin_of("http://localhost:8081", allow_insecure_loopback=True) == (
            "http",
            "localhost",
            8081,
        )
        with pytest.raises(ValueError, match="https"):
            origin_of("http://localhost:8081", allow_insecure_loopback=False)
        for url in ("http://idp.example.com", "http://10.0.0.1", "http://localhost.evil.com"):
            with pytest.raises(ValueError, match="https"):
                origin_of(url, allow_insecure_loopback=True)

    def test_config_validates_the_issuer(self) -> None:
        with pytest.raises(ValueError, match="https"):
            OidcClientConfig("http://idp.example.com", "c", "s", "https://x/cb")


class TestProxy:
    PROXY = "http://egress.internal:3128"

    def test_connects_to_the_proxy_and_tunnels_to_the_issuer(self) -> None:
        connection = FakeConnection(FakeResponse(200, b"{}"))
        t, made = transport(connection, proxy_url=self.PROXY)
        assert t.send(url="https://idp.example.com/a", **SEND).status == 200
        assert made == [("https", "egress.internal", 3128, 5.0)]
        assert connection.tunnel == ("idp.example.com", 443)
        assert connection.requests == [("GET", "/a")]

    def test_without_a_proxy_there_is_no_tunnel(self) -> None:
        connection = FakeConnection(FakeResponse(200, b"{}"))
        t, _ = transport(connection)
        t.send(url="https://idp.example.com/a", **SEND)
        assert connection.tunnel is None

    def test_the_proxy_does_not_widen_the_allowed_origins(self) -> None:
        connection = FakeConnection(FakeResponse(200, b"{}"))
        t, made = transport(connection, proxy_url=self.PROXY)
        with pytest.raises(OidcTransportError, match="outside"):
            t.send(url="https://evil.example.com/a", **SEND)
        assert made == []

    @pytest.mark.parametrize(
        "url",
        [
            "https://egress.internal:3128",
            "http://egress.internal",
            "http://user:pw@egress.internal:3128",
            "http://egress.internal:3128/path",
            "http://egress.internal:3128/?q=1",
            "http://egress.internal:3128/#f",
            "egress.internal:3128",
            "http://:3128",
        ],
    )
    def test_invalid_proxy_urls_are_refused(self, url: str) -> None:
        with pytest.raises(ValueError, match="proxy"):
            HttpOidcTransport(origin_url="https://idp.example.com", proxy_url=url)
        with pytest.raises(ValueError, match="proxy"):
            OidcClientConfig(
                issuer="https://idp.example.com",
                client_id="c",
                client_secret="s",
                redirect_uri="https://nave.example.com/api/v1/auth/callback",
                proxy_url=url,
            )

    def test_a_proxy_is_not_allowed_with_a_plain_http_dev_issuer(self) -> None:
        with pytest.raises(ValueError, match="https"):
            HttpOidcTransport(
                origin_url="http://localhost:8080",
                allow_insecure_loopback=True,
                proxy_url=self.PROXY,
            )
        with pytest.raises(ValueError, match="https"):
            OidcClientConfig(
                issuer="http://localhost:8080",
                client_id="c",
                client_secret="s",
                redirect_uri="http://localhost/cb",
                allow_insecure_loopback=True,
                proxy_url=self.PROXY,
            )
