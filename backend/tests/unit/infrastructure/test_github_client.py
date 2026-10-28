import http.client
import ssl
from datetime import timedelta
from typing import Any

import pytest

from nave.application.errors import VcsRejectedError, VcsUnavailableError
from nave.domain.errors import InvalidValueError
from nave.infrastructure.vcs.github_http import (
    DEFAULT_RATE_LIMIT_WAIT,
    GITHUB_API_VERSION,
    MAX_RETRY_AFTER,
    GithubRequest,
    HttpsGithubTransport,
    RawResponse,
    TransportError,
    _default_connection,
)
from tests.fakes.github import api, response
from tests.fakes.memory import FakeClock

BEARER = "B" * 40


def get(path: str = "/app/installations/7", **kwargs: Any) -> GithubRequest:
    return GithubRequest(method="GET", path=path, bearer=BEARER, **kwargs)


class TestRequestsAndReplies:
    def test_sends_the_standard_headers_and_a_compact_json_body(self) -> None:
        client, transport, _ = api(response(201, {"ok": True}))
        client.send(
            GithubRequest(
                method="POST",
                path="/x",
                bearer=BEARER,
                json_body={"a": [1, 2], "b": "é"},
                expected=frozenset({201}),
            )
        )
        call = transport.calls[0]
        assert call.method == "POST"
        assert call.headers["Authorization"] == f"Bearer {BEARER}"
        assert call.headers["Accept"] == "application/vnd.github+json"
        assert call.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
        assert call.headers["User-Agent"]
        assert call.headers["Content-Type"] == "application/json"
        assert call.body == b'{"a":[1,2],"b":"\\u00e9"}'

    def test_a_request_without_body_has_no_content_type(self) -> None:
        client, transport, _ = api(response(200, {}))
        client.send(get())
        assert "Content-Type" not in transport.calls[0].headers
        assert transport.calls[0].body is None

    def test_decodes_json_and_empty_bodies(self) -> None:
        client, _, _ = api(response(200, {"id": 7}), response(204))
        assert client.send(get()).body == {"id": 7}
        assert client.send(get(expected=frozenset({204}))).body is None

    def test_an_expected_error_status_is_returned_not_raised(self) -> None:
        client, _, _ = api(response(404, {"message": "Not Found"}))
        reply = client.send(get(expected=frozenset({200, 404})))
        assert reply.status == 404

    def test_the_response_limit_is_passed_to_the_transport(self) -> None:
        client, transport, _ = api(response(200, {}))
        client.send(get(max_response_bytes=1234))
        assert transport.calls[0].max_response_bytes == 1234

    @pytest.mark.parametrize("body", [b"{not json", b"[" * 100_000])
    def test_invalid_or_hostile_json_in_a_success_response_is_rejected(self, body: bytes) -> None:
        client, _, _ = api(RawResponse(200, body, {}))
        with pytest.raises(VcsRejectedError):
            client.send(get())


class TestErrorMapping:
    @pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 404, 409, 410, 422])
    def test_client_errors_are_permanent(self, status: int) -> None:
        client, _, _ = api(response(status, {"message": "nope"}))
        with pytest.raises(VcsRejectedError) as raised:
            client.send(get())
        assert raised.value.status == status

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_server_errors_are_transient(self, status: int) -> None:
        client, _, _ = api(response(status))
        with pytest.raises(VcsUnavailableError) as raised:
            client.send(get())
        assert raised.value.retry_after is None

    def test_a_transport_failure_is_transient(self) -> None:
        client, _, _ = api(TransportError("timeout"))
        with pytest.raises(VcsUnavailableError):
            client.send(get())

    def test_429_honours_retry_after(self) -> None:
        client, _, _ = api(response(429, headers={"retry-after": "42"}))
        with pytest.raises(VcsUnavailableError) as raised:
            client.send(get())
        assert raised.value.retry_after == timedelta(seconds=42)

    def test_secondary_limit_403_with_retry_after_is_transient(self) -> None:
        client, _, _ = api(response(403, headers={"retry-after": "30"}))
        with pytest.raises(VcsUnavailableError) as raised:
            client.send(get())
        assert raised.value.retry_after == timedelta(seconds=30)

    def test_primary_limit_waits_until_the_reset(self) -> None:
        clock = FakeClock()
        reset = int(clock.now().timestamp()) + 90
        client, _, _ = api(
            response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)}),
            clock=clock,
        )
        with pytest.raises(VcsUnavailableError) as raised:
            client.send(get())
        assert raised.value.retry_after == timedelta(seconds=90)

    def test_a_limit_without_headers_is_recognised_by_its_message(self) -> None:
        client, _, _ = api(response(403, {"message": "You have exceeded a secondary rate limit."}))
        with pytest.raises(VcsUnavailableError) as raised:
            client.send(get())
        assert raised.value.retry_after == DEFAULT_RATE_LIMIT_WAIT

    def test_a_reset_already_in_the_past_falls_back_to_the_default_wait(self) -> None:
        clock = FakeClock()
        past = int(clock.now().timestamp()) - 10
        client, _, _ = api(
            response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(past)}),
            clock=clock,
        )
        with pytest.raises(VcsUnavailableError) as raised:
            client.send(get())
        assert raised.value.retry_after == DEFAULT_RATE_LIMIT_WAIT

    def test_a_403_with_an_unparseable_body_is_a_permission_problem(self) -> None:
        client, _, _ = api(RawResponse(403, b"<html>nope</html>", {}))
        with pytest.raises(VcsRejectedError):
            client.send(get())

    def test_the_wait_is_capped(self) -> None:
        client, _, _ = api(response(429, headers={"retry-after": "999999"}))
        with pytest.raises(VcsUnavailableError) as raised:
            client.send(get())
        assert raised.value.retry_after == MAX_RETRY_AFTER

    @pytest.mark.parametrize("value", ["abc", "-5", "1e3", "", "99999999999"])
    def test_a_malformed_retry_after_falls_back_to_the_default(self, value: str) -> None:
        client, _, _ = api(response(429, headers={"retry-after": value}))
        with pytest.raises(VcsUnavailableError) as raised:
            client.send(get())
        assert raised.value.retry_after in {DEFAULT_RATE_LIMIT_WAIT, MAX_RETRY_AFTER}

    def test_a_plain_403_is_a_permission_problem_not_a_rate_limit(self) -> None:
        client, _, _ = api(response(403, {"message": "Resource not accessible by integration"}))
        with pytest.raises(VcsRejectedError):
            client.send(get())

    def test_the_request_id_is_kept_only_when_well_formed(self) -> None:
        client, _, _ = api(
            response(404, headers={"x-github-request-id": "AB12:34CD:5678:9EF0:1234ABCD"}),
            response(404, headers={"x-github-request-id": "bad id\r\nX: y"}),
        )
        with pytest.raises(VcsRejectedError) as good:
            client.send(get())
        with pytest.raises(VcsRejectedError) as bad:
            client.send(get())
        assert good.value.request_id == "AB12:34CD:5678:9EF0:1234ABCD"
        assert bad.value.request_id is None

    def test_errors_never_echo_the_remote_text_or_the_credential(self) -> None:
        secret_text = "SECRET-REMOTE-TEXT"
        client, _, _ = api(
            response(422, {"message": secret_text}),
            response(500, {"message": secret_text}),
            response(403, {"message": f"rate limit {secret_text}"}),
        )
        for _ in range(3):
            with pytest.raises((VcsRejectedError, VcsUnavailableError)) as raised:
                client.send(get())
            assert secret_text not in str(raised.value)
            assert secret_text not in repr(raised.value)
            assert BEARER not in str(raised.value)


class TestRequestValidation:
    @pytest.mark.parametrize("method", ["", "get", "PUT", "TRACE", "GET "])
    def test_method(self, method: str) -> None:
        with pytest.raises(InvalidValueError):
            GithubRequest(method=method, path="/x", bearer=BEARER)

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "x",
            "https://evil.example/x",
            "//evil.example/x",
            "/a/../b",
            "/a//b",
            "/a b",
            "/a\r\nHost: evil",
            "/a\x00",
            "/%2F",
            "/%2f..",
            "/é",
            "/" + "a" * 2000,
        ],
    )
    def test_path(self, path: str) -> None:
        with pytest.raises(InvalidValueError):
            GithubRequest(method="GET", path=path, bearer=BEARER)

    @pytest.mark.parametrize("bearer", ["", "short", "a b" * 10, "x\n" * 10, "é" * 20, "x" * 2049])
    def test_bearer(self, bearer: str) -> None:
        with pytest.raises(InvalidValueError):
            GithubRequest(method="GET", path="/x", bearer=bearer)

    def test_expected_statuses_are_required(self) -> None:
        with pytest.raises(InvalidValueError):
            GithubRequest(method="GET", path="/x", bearer=BEARER, expected=frozenset())

    def test_the_bearer_is_not_in_the_repr(self) -> None:
        assert BEARER not in repr(get())

    def test_valid_paths(self) -> None:
        for path in (
            "/app/installations/7/access_tokens",
            "/repos/acme/app/check-runs?per_page=100",
        ):
            assert get(path).path == path


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: list[tuple[str, str]]) -> None:
        self.status = status
        self._body = body
        self._headers = headers

    def read(self, amt: int) -> bytes:
        return self._body[:amt]

    def getheaders(self) -> list[tuple[str, str]]:
        return self._headers


class FakeConnection:
    instances: list["FakeConnection"] = []  # noqa: RUF012

    def __init__(self, host: str, port: int, timeout: float, context: ssl.SSLContext) -> None:
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.tunnel: tuple[str, int] | None = None
        self.sent: tuple[str, str, bytes | None, dict[str, str]] | None = None
        self.closed = False
        self.reply: FakeResponse | Exception = FakeResponse(
            200,
            b'{"ok":true}',
            [
                ("Link", "<x>"),
                ("X-RateLimit-Remaining", "9"),
                ("Set-Cookie", "a=b"),
                ("Server", "x"),
            ],
        )
        FakeConnection.instances.append(self)

    def set_tunnel(self, host: str, port: int) -> None:
        self.tunnel = (host, port)

    def request(self, method: str, path: str, body: bytes | None, headers: dict[str, str]) -> None:
        self.sent = (method, path, body, headers)

    def getresponse(self) -> FakeResponse:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def close(self) -> None:
        self.closed = True


def factory(reply: FakeResponse | Exception | None = None) -> Any:
    def make(host: str, port: int, timeout: float, context: ssl.SSLContext) -> FakeConnection:
        connection = FakeConnection(host, port, timeout, context)
        if reply is not None:
            connection.reply = reply
        return connection

    return make


def send(transport: HttpsGithubTransport, max_bytes: int = 1000) -> Any:
    return transport.send(
        method="GET",
        path="/x",
        headers={"A": "b"},
        body=None,
        timeout=timedelta(seconds=7),
        max_response_bytes=max_bytes,
    )


class TestHttpsTransport:
    def setup_method(self) -> None:
        FakeConnection.instances.clear()

    def test_connects_directly_with_a_verifying_tls_context(self) -> None:
        raw = send(HttpsGithubTransport(connection_factory=factory()))
        connection = FakeConnection.instances[0]
        assert (connection.host, connection.port, connection.timeout) == ("api.github.com", 443, 7)
        assert connection.tunnel is None
        assert connection.context.verify_mode == ssl.CERT_REQUIRED
        assert connection.context.check_hostname
        assert connection.sent == ("GET", "/x", None, {"A": "b"})
        assert connection.closed
        assert raw.status == 200

    def test_only_the_headers_the_client_uses_are_kept_in_lowercase(self) -> None:
        raw = send(HttpsGithubTransport(connection_factory=factory()))
        assert dict(raw.headers) == {"link": "<x>", "x-ratelimit-remaining": "9"}

    def test_goes_through_the_egress_proxy_with_connect(self) -> None:
        send(
            HttpsGithubTransport(proxy_url="http://egress-proxy:3128", connection_factory=factory())
        )
        connection = FakeConnection.instances[0]
        assert (connection.host, connection.port) == ("egress-proxy", 3128)
        assert connection.tunnel == ("api.github.com", 443)

    def test_a_response_over_the_limit_is_refused(self) -> None:
        reply = FakeResponse(200, b"x" * 2000, [])
        with pytest.raises(TransportError):
            send(HttpsGithubTransport(connection_factory=factory(reply)), max_bytes=1000)
        assert FakeConnection.instances[0].closed

    @pytest.mark.parametrize(
        "error", [OSError("boom"), http.client.BadStatusLine("x"), TimeoutError()]
    )
    def test_network_failures_become_transport_errors(self, error: Exception) -> None:
        with pytest.raises(TransportError):
            send(HttpsGithubTransport(connection_factory=factory(error)))
        assert FakeConnection.instances[0].closed

    @pytest.mark.parametrize(
        "url",
        [
            "http://api.github.com",
            "https://user:pw@api.github.com",
            "https://api.github.com/v3",
            "https://api.github.com?x=1",
            "https://",
            "ftp://api.github.com",
            "",
        ],
    )
    def test_the_base_url_is_validated(self, url: str) -> None:
        with pytest.raises(InvalidValueError):
            HttpsGithubTransport(base_url=url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://proxy:3128",
            "http://proxy",
            "http://u:p@proxy:3128",
            "http://proxy:3128/x",
            "proxy:3128",
        ],
    )
    def test_the_proxy_url_is_validated(self, url: str) -> None:
        with pytest.raises(InvalidValueError):
            HttpsGithubTransport(proxy_url=url)

    def test_the_default_connection_factory_builds_a_verifying_https_connection(self) -> None:
        context = ssl.create_default_context()
        connection = _default_connection("api.github.com", 443, 5.0, context)
        assert isinstance(connection, http.client.HTTPSConnection)
        assert (connection.host, connection.port, connection.timeout) == (
            "api.github.com",
            443,
            5.0,
        )
