import base64
import io

import pytest

from nave_fetch import git_environment, read_token

OLD = "ghs_" + "T" * 36
# Formato nuevo (ADR 0013): ~520 caracteres, con puntos; se trata como opaco.
NEW = "ghs_12345678_eyJhbGciOiJSUzI1NiJ9." + "A" * 460 + "." + "B" * 43


@pytest.mark.parametrize("token", [OLD, NEW])
def test_both_installation_token_formats_are_read_whole(token: str) -> None:
    assert read_token(io.StringIO(token + "\n")) == token


@pytest.mark.parametrize(
    "text",
    ["", "\n", "short\n", "ghs_" + "x" * 1030 + "\n", "ghs_ " + "x" * 30 + "\n", "a b" * 10 + "\n"],
)
def test_malformed_tokens_are_refused(text: str) -> None:
    assert read_token(io.StringIO(text)) is None


def test_the_token_only_travels_in_the_git_environment() -> None:
    credentials = base64.b64encode(f"x-access-token:{NEW}".encode("ascii")).decode("ascii")
    env = git_environment(NEW)
    assert any(credentials in value for value in env.values())
    assert all(NEW not in value for value in env.values())
