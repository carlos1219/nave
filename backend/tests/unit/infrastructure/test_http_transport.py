import socket
import threading
from collections.abc import Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nave.infrastructure.sandbox.runner_client import HttpTransport, TransportError

SEEN: dict[str, object] = {}


class Handler(BaseHTTPRequestHandler):
    reply_status = 200
    reply_body = b'{"status":"ok"}'
    reply_headers: dict[str, str] = {}  # noqa: RUF012

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        SEEN["body"] = self.rfile.read(length)
        SEEN["path"] = self.path
        SEEN["signature"] = self.headers.get("X-Nave-Signature")
        self.send_response(self.reply_status)
        for name, value in self.reply_headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(self.reply_body)))
        self.end_headers()
        self.wfile.write(self.reply_body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture
def server() -> Iterator[tuple[str, type[Handler]]]:
    handler = type("H", (Handler,), {})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", handler
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def post(url: str, max_bytes: int = 1000) -> object:
    return HttpTransport(url).post(
        headers={"X-Nave-Signature": "sig"},
        body=b'{"a":1}',
        timeout=timedelta(seconds=5),
        max_response_bytes=max_bytes,
    )


def test_posts_to_the_fixed_path_and_returns_status_and_body(
    server: tuple[str, type[Handler]],
) -> None:
    url, _ = server
    response = post(url)
    assert response.status == 200  # type: ignore[attr-defined]
    assert response.body == b'{"status":"ok"}'  # type: ignore[attr-defined]
    assert SEEN["path"] == "/v1/runs"
    assert SEEN["body"] == b'{"a":1}'
    assert SEEN["signature"] == "sig"


def test_retry_after_header_is_passed_through(server: tuple[str, type[Handler]]) -> None:
    url, handler = server
    handler.reply_status = 429
    handler.reply_headers = {"Retry-After": "12"}
    response = post(url)
    assert response.status == 429  # type: ignore[attr-defined]
    assert response.retry_after == "12"  # type: ignore[attr-defined]


def test_redirects_are_not_followed(server: tuple[str, type[Handler]]) -> None:
    url, handler = server
    handler.reply_status = 302
    handler.reply_headers = {"Location": "http://169.254.169.254/"}
    assert post(url).status == 302  # type: ignore[attr-defined]


def test_an_oversized_response_is_refused(server: tuple[str, type[Handler]]) -> None:
    url, handler = server
    handler.reply_body = b"x" * 2000
    with pytest.raises(TransportError):
        post(url, max_bytes=1000)


def test_connection_refused_is_a_transport_error() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(TransportError):
        post(f"http://127.0.0.1:{port}")


def test_a_silent_server_times_out() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with pytest.raises(TransportError):
            HttpTransport(f"http://127.0.0.1:{port}").post(
                headers={},
                body=b"{}",
                timeout=timedelta(milliseconds=200),
                max_response_bytes=10,
            )
