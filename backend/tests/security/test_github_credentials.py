"""AM-803, AM-506, AM-306: ni la clave de la App, ni el JWT, ni los tokens de instalación
aparecen en logs, `repr` o mensajes de error."""

import logging

import pytest

from nave.application.errors import ApplicationError
from nave.domain.errors import InvalidValueError
from nave.domain.value_objects.repo_ref import RepoRef
from nave.infrastructure.observability.logging import redact
from nave.infrastructure.vcs.app_jwt import AppJwtSigner, AppPrivateKey
from nave.infrastructure.vcs.github_http import GithubApiClient, GithubRequest, TransportError
from nave.infrastructure.vcs.installation_tokens import GithubInstallationTokens
from tests.fakes.github import (
    ISSUER,
    LONG_TOKEN,
    TOKEN,
    FakeGithubTransport,
    generate_key,
    response,
    signer,
    to_pem,
)
from tests.fakes.memory import FakeClock

KEY = generate_key()
PEM = to_pem(KEY)
BODY_LINE = PEM.splitlines()[1]
REPO = RepoRef("acme", "app")


def test_the_private_key_never_appears_in_repr_or_errors() -> None:
    key = AppPrivateKey.from_pem(PEM)
    clock = FakeClock()
    jwt_signer = AppJwtSigner(issuer=ISSUER, key=key, clock=clock)
    for text in (repr(key), str(key), repr(jwt_signer), str(jwt_signer)):
        assert BODY_LINE not in text
        assert "PRIVATE KEY" not in text
    broken = PEM.replace(BODY_LINE, "AAAA" + BODY_LINE[4:])
    with pytest.raises(InvalidValueError) as raised:
        AppPrivateKey.from_pem(broken)
    assert BODY_LINE not in str(raised.value)
    assert "PRIVATE KEY" not in str(raised.value)


def test_the_jwt_is_not_in_the_signer_repr() -> None:
    jwt_signer = signer(FakeClock())
    assert jwt_signer.token() not in repr(jwt_signer)


@pytest.mark.parametrize("token", [TOKEN, LONG_TOKEN])
def test_installation_tokens_are_redacted_from_log_lines(token: str) -> None:
    assert token not in redact(f"clone failed with {token} in the message")


@pytest.mark.parametrize("token", [TOKEN, LONG_TOKEN])
def test_no_log_or_error_carries_the_installation_token(
    token: str, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock()
    body = {
        "token": token,
        "expires_at": "2026-10-11T09:00:00Z",
        "permissions": {"contents": "read"},
        "repository_selection": "selected",
        "repositories": [{"name": "app"}],
    }
    transport = FakeGithubTransport(
        response(201, body), TransportError("connection reset"), response(403, {"message": token})
    )
    tokens = GithubInstallationTokens(
        api=GithubApiClient(transport=transport, clock=clock),
        signer=signer(clock),
        clock=clock,
        sarif_upload=False,
    )
    with caplog.at_level(logging.DEBUG):
        with tokens.fetch_token(installation_id=7, repo=REPO):
            pass
        with pytest.raises(ApplicationError) as raised:
            tokens.fetch_token(installation_id=7, repo=REPO).__enter__()
        assert token not in str(raised.value)
    assert token not in caplog.text


def test_the_bearer_is_never_echoed_by_a_request_error() -> None:
    bearer = "Z" * 40
    client = GithubApiClient(
        transport=FakeGithubTransport(TransportError("down"), response(500), response(404)),
        clock=FakeClock(),
    )
    for _ in range(3):
        with pytest.raises(ApplicationError) as raised:
            client.send(GithubRequest(method="GET", path="/x", bearer=bearer))
        assert bearer not in str(raised.value)
        assert bearer not in repr(raised.value)


def test_a_mint_request_is_authenticated_as_the_app_and_a_revoke_as_the_token() -> None:
    clock = FakeClock()
    body = {
        "token": TOKEN,
        "expires_at": "2026-10-11T09:00:00Z",
        "permissions": {"contents": "read"},
        "repository_selection": "selected",
    }
    transport = FakeGithubTransport(response(201, body), response(204))
    tokens = GithubInstallationTokens(
        api=GithubApiClient(transport=transport, clock=clock),
        signer=signer(clock),
        clock=clock,
        sarif_upload=False,
    )
    with tokens.fetch_token(installation_id=7, repo=REPO):
        pass
    mint, revoke = transport.calls
    assert mint.bearer != TOKEN
    assert mint.bearer.count(".") == 2
    assert revoke.bearer == TOKEN
