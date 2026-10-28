"""F7-29: la ruta decodificada no puede forjar líneas en el log de acceso de la API."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from gunicorn.config import Config

from nave.infrastructure.observability.gunicorn_logger import AccessLogger, sanitize_log_value

ACCESS_FORMAT = '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(L)s'


def test_control_characters_quotes_and_backslashes_are_escaped() -> None:
    hostile = '/a\nINJECTED"x\\y\r' + chr(0) + chr(0x7F) + chr(0x202E)
    out = sanitize_log_value(hostile)
    assert out == "/a\\x0aINJECTED\\x22x\\x5cy\\x0d\\x00\\x7f\\u202e"
    assert "\n" not in out
    assert "\r" not in out


def test_secrets_in_the_path_are_redacted() -> None:
    assert "SECRET" not in sanitize_log_value("/api/v1/x/ghs_" + "SECRET" * 6)


def test_harmless_paths_are_unchanged() -> None:
    assert sanitize_log_value("/api/v1/organizations/abc/members") == (
        "/api/v1/organizations/abc/members"
    )


@pytest.mark.parametrize("path", ["/x\nFORGED 200", "/x\r\nFORGED 200"])
def test_access_line_is_single_line(path: str, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = Config()
    cfg.set("accesslog", "-")
    cfg.set("access_log_format", ACCESS_FORMAT)
    logger = AccessLogger(cfg)
    request: Any = SimpleNamespace(
        method="GET",
        path=path,
        uri=path,
        query="token=ghs_" + "Q" * 30,
        headers=[],
        version=(1, 1),
        scheme="https",
    )
    response: Any = SimpleNamespace(
        status="404", status_code=404, sent=10, headers=[], response_length=10
    )
    environ = {
        "REMOTE_ADDR": "10.0.0.1",
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "RAW_URI": "/x",
        "QUERY_STRING": request.query,
        "SERVER_PROTOCOL": "HTTP/1.1",
    }
    logger.access(response, request, environ, request_time=timedelta(0))
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "\n" not in lines[0]
    assert "\r" not in lines[0]
    assert "Q" * 10 not in lines[0]
