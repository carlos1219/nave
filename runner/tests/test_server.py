import contextlib
import http.client
import json
import socket
import threading
import time
from collections.abc import Iterator

import pytest

from nave_runner.engine import EngineError
from nave_runner.protocol import compute_signature
from nave_runner.server import RunnerHTTPServer
from tests.conftest import KEY, RUN, TOKEN, FakeEngine, make_config

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
RELEASE = {"step": "release", "run_id": RUN}


@pytest.fixture
def server(engine: FakeEngine) -> Iterator[RunnerHTTPServer]:
    httpd = RunnerHTTPServer(make_config(), engine)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def signed(
    document: object,
    *,
    nonce: str | None = None,
    timestamp: int | None = None,
    key: bytes = KEY,
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    ts = int(time.time()) if timestamp is None else timestamp
    n = nonce or f"{time.time_ns():032x}"[-32:]
    return body, {
        "Content-Type": "application/json",
        "X-Nave-Timestamp": str(ts),
        "X-Nave-Nonce": n,
        "X-Nave-Signature": compute_signature(
            key, method="POST", path="/v1/runs", timestamp=ts, nonce=n, body=body
        ),
    }


def request(
    server: RunnerHTTPServer,
    body: bytes,
    headers: dict[str, str],
    *,
    method: str = "POST",
    path: str = "/v1/runs",
) -> tuple[int, dict[str, object], http.client.HTTPResponse]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request(method, path, body=body or None, headers=headers)
        response = connection.getresponse()
        data = response.read()
        return response.status, (json.loads(data) if data else {}), response
    finally:
        connection.close()


def post(
    server: RunnerHTTPServer, document: object, **kwargs: object
) -> tuple[int, dict[str, object]]:
    body, headers = signed(document, **kwargs)  # type: ignore[arg-type]
    status, payload, _ = request(server, body, headers)
    return status, payload


def test_authenticated_release_succeeds(server: RunnerHTTPServer, engine: FakeEngine) -> None:
    engine.workspaces.add(RUN)
    assert post(server, RELEASE) == (200, {"status": "ok"})
    assert RUN not in engine.workspaces


def test_fetch_runs_the_container_with_the_token_on_stdin(
    server: RunnerHTTPServer, engine: FakeEngine
) -> None:
    assert post(server, FETCH) == (200, {"status": "ok"})
    assert engine.runs[0][1] == (TOKEN + "\n").encode()


def test_replayed_request_is_rejected(server: RunnerHTTPServer) -> None:
    body, headers = signed(RELEASE)
    assert request(server, body, headers)[0] == 200
    assert request(server, body, headers)[0] == 401


def test_all_authentication_failures_look_the_same(server: RunnerHTTPServer) -> None:
    body, good = signed(RELEASE)
    variants = [
        {k: v for k, v in good.items() if k != "X-Nave-Signature"},
        {**good, "X-Nave-Signature": "0" * 64},
        {**good, "X-Nave-Timestamp": "1"},
        {**good, "X-Nave-Nonce": "zz"},
        signed(RELEASE, key=bytes(32))[1],
        signed(RELEASE, timestamp=int(time.time()) - 3600)[1],
    ]
    replies = [request(server, body, v)[:2] for v in variants]
    assert all(r == (401, {"error": "unauthorized"}) for r in replies)


def test_nothing_is_executed_for_unauthenticated_requests(
    server: RunnerHTTPServer, engine: FakeEngine
) -> None:
    body, _ = signed(FETCH)
    request(server, body, {"X-Nave-Signature": "0" * 64})
    assert not engine.runs
    assert not engine.workspaces


def test_signature_does_not_cover_a_different_body(server: RunnerHTTPServer) -> None:
    _, headers = signed(RELEASE)
    other, _ = signed({**RELEASE, "run_id": "f" * 32})
    assert request(server, other, headers)[0] == 401


def test_validation_happens_after_authentication(server: RunnerHTTPServer) -> None:
    assert post(server, {"step": "fetch", "image": "evil"}) == (400, {"error": "bad_request"})
    assert post(server, {**FETCH, "network": "host"}) == (400, {"error": "bad_request"})


def test_rejections_are_logged_with_the_rule_but_no_values(
    server: RunnerHTTPServer, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="nave_runner.http"):
        assert post(server, {**FETCH, "token": "ghs_" + "Z" * 10})[0] == 400
    (record,) = [r for r in caplog.records if r.getMessage() == "request rejected"]
    assert record.outcome == "invalid token"  # type: ignore[attr-defined]
    assert "Z" * 10 not in caplog.text


def test_oversized_body_is_refused_before_reading_it(server: RunnerHTTPServer) -> None:
    with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as s:
        s.sendall(b"POST /v1/runs HTTP/1.1\r\nHost: x\r\nContent-Length: 999999\r\n\r\n")
        assert s.recv(200).startswith(b"HTTP/1.0 413")


@pytest.mark.parametrize(
    "extra",
    [b"Transfer-Encoding: chunked\r\n", b"Content-Length: -5\r\n", b"Content-Length: abc\r\n", b""],
)
def test_framing_attacks_are_rejected(server: RunnerHTTPServer, extra: bytes) -> None:
    with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as s:
        s.sendall(b"POST /v1/runs HTTP/1.1\r\nHost: x\r\n" + extra + b"\r\n0\r\n\r\n")
        assert s.recv(200).startswith(b"HTTP/1.0 400")


def test_a_truncated_body_gets_no_processing(server: RunnerHTTPServer, engine: FakeEngine) -> None:
    with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as s:
        s.sendall(b"POST /v1/runs HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{}")
        s.shutdown(socket.SHUT_WR)
        s.settimeout(2)
        with contextlib.suppress(OSError):
            s.recv(100)
    assert not engine.runs


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/v1/runs"), ("POST", "/"), ("POST", "/v1/runs/x")]
)
def test_other_routes_are_not_found(server: RunnerHTTPServer, method: str, path: str) -> None:
    status, _, _ = request(server, b"{}" if method == "POST" else b"", {}, method=method, path=path)
    assert status in {404, 501}


def test_busy_returns_429_with_retry_after(engine: FakeEngine) -> None:
    config = make_config(max_concurrent=1)
    httpd = RunnerHTTPServer(config, engine)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        httpd.service._slots.acquire()
        body, headers = signed(FETCH)
        status, payload, response = request(httpd, body, headers)
        assert (status, payload) == (429, {"error": "busy"})
        assert response.getheader("Retry-After") == "15"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_responses_carry_hardening_headers_and_no_server_banner(server: RunnerHTTPServer) -> None:
    body, headers = signed(RELEASE)
    _, _, response = request(server, body, headers)
    assert response.getheader("Cache-Control") == "no-store"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert "Python" not in (response.getheader("Server") or "")


def test_healthz_reflects_the_engine(server: RunnerHTTPServer, engine: FakeEngine) -> None:
    assert request(server, b"", {}, method="GET", path="/healthz")[:2] == (200, {"status": "ok"})
    engine.healthy = False
    assert request(server, b"", {}, method="GET", path="/healthz")[0] == 503


def test_error_bodies_never_contain_internal_details(
    server: RunnerHTTPServer, engine: FakeEngine
) -> None:
    engine.results.append(EngineError("/var/run/docker.sock: permission denied"))
    status, payload = post(server, FETCH)
    assert status == 503
    assert payload == {"error": "fetch_unavailable"}
