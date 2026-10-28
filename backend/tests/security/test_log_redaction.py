"""AM-306: ningún secreto sale por los logs, aunque alguien lo pase por descuido."""

import json
import logging
import sys

import pytest

from nave.infrastructure.observability.logging import JsonFormatter, redact


def render(message: str, *args: object, **extra: object) -> dict[str, object]:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, message, args, None)
    for key, value in extra.items():
        setattr(record, key, value)
    line = JsonFormatter("nave-worker").format(record)
    assert "\n" not in line
    parsed: dict[str, object] = json.loads(line)
    return parsed


@pytest.mark.parametrize(
    "secret_text",
    [
        "Authorization: Bearer abcdefghijklmnop123456",
        "token ghs_" + "A" * 36,
        "installation ghs_12345678_eyJhbGciOiJSUzI1NiJ9." + "C" * 460 + "." + "D" * 43,
        "github_pat_" + "B" * 30,
        "password=hunter2",
        "api_key: sk-123456",
        "postgresql://nave_app:hunter2@postgres/nave",
        "redis://default:hunter2@redis:6379/0",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_secrets_in_messages_are_redacted(secret_text: str) -> None:
    out = json.dumps(render("failed: %s", secret_text))
    for leaked in (
        "abcdefghijklmnop123456",
        "hunter2",
        "A" * 36,
        "B" * 30,
        "C" * 460,
        "D" * 43,
        "sk-123456",
        "RSA",
    ):
        assert leaked not in out
    assert "[REDACTED]" in out


@pytest.mark.parametrize(
    "secret_text",
    [
        "jwt eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl",
        "cookie __Host-nave_session=Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA",
    ],
)
def test_jwt_and_session_cookie_are_redacted(secret_text: str) -> None:
    out = json.dumps(render("seen %s", secret_text))
    assert "eyJzdWIi" not in out
    assert "Zm9vYmFy" not in out
    assert "[REDACTED]" in out


def test_secret_straddling_the_truncation_limit_is_not_leaked() -> None:
    secret = "ghs_" + "S" * 40
    out = str(render("a" * 1990 + secret)["message"])
    assert "ghs_" not in out
    assert "S" * 6 not in out


def test_redact_keeps_harmless_text() -> None:
    assert redact("scan finished in 12s") == "scan finished in 12s"


def test_only_allowlisted_fields_are_emitted() -> None:
    out = render("x", scan_id="s1", installation_token="ghs_secret", password="p", retries=2)
    assert out["scan_id"] == "s1"
    assert out["retries"] == 2
    assert "installation_token" not in out
    assert "password" not in out


def test_extra_values_are_redacted_too() -> None:
    assert "hunter2" not in json.dumps(render("x", error="password=hunter2"))


def test_newlines_cannot_forge_log_lines() -> None:
    out = render("hello\n" + json.dumps({"level": "CRITICAL", "message": "forged"}))
    assert out["level"] == "INFO"


def test_oversized_messages_are_truncated() -> None:
    assert len(str(render("a" * 100_000)["message"])) <= 2000


def test_exceptions_log_type_and_frames_but_never_the_message_or_locals() -> None:

    def fail() -> None:
        secret_local = "hunter2"  # noqa: F841
        raise ValueError("connection string postgresql://u:hunter2@h/db")

    try:
        fail()
    except ValueError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    out = JsonFormatter("nave-api").format(record)
    assert "hunter2" not in out
    assert json.loads(out)["exc_type"] == "ValueError"
    assert json.loads(out)["stack"]


def test_service_and_timestamp_are_always_present() -> None:
    out = render("x")
    assert out["service"] == "nave-worker"
    assert str(out["ts"]).endswith("+00:00")
