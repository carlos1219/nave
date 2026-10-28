"""AM-102: CSRF en peticiones no seguras con sesión (ADR 0016)."""

import pytest
from flask.testing import FlaskClient

from nave.delivery.http.security import CSRF_HEADER
from tests.fakes.auth import PUBLIC_ORIGIN, TestApp, make_test_app

BASE = {"base_url": PUBLIC_ORIGIN}
URL = "/api/v1/auth/logout"


def signed_in() -> tuple[TestApp, FlaskClient, str]:
    t = make_test_app()
    client = t.browser()
    t.sign_in(client)
    return t, client, t.csrf_token(client)


def test_a_correct_origin_and_token_pass() -> None:
    _t, client, csrf = signed_in()
    response = client.post(URL, headers={"Origin": PUBLIC_ORIGIN, CSRF_HEADER: csrf}, **BASE)
    assert response.status_code == 204


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "https://evil.example",
        "https://nave.test.evil.example",
        "http://nave.test",
        "https://nave.test:8443",
        "https://NAVE.test",
        "https://nave.test/",
        "",
    ],
)
def test_any_other_origin_is_rejected_even_with_the_right_token(origin: str | None) -> None:
    t, client, csrf = signed_in()
    headers = {CSRF_HEADER: csrf}
    if origin is not None:
        headers["Origin"] = origin
    response = client.post(URL, headers=headers, **BASE)
    assert response.status_code == 403
    assert len(t.store.state.sessions) == 1  # nada cambió


@pytest.mark.parametrize("token", [None, "", "x", "a" * 500, "WRONG"])
def test_a_missing_or_wrong_token_is_rejected_even_from_the_right_origin(
    token: str | None,
) -> None:
    t, client, _ = signed_in()
    headers = {"Origin": PUBLIC_ORIGIN}
    if token is not None:
        headers[CSRF_HEADER] = token
    response = client.post(URL, headers=headers, **BASE)
    assert response.status_code == 403
    assert len(t.store.state.sessions) == 1


def test_the_token_of_another_session_is_rejected() -> None:
    t = make_test_app()
    mine, other = t.browser(), t.browser()
    t.sign_in(mine)
    t.sign_in(other)
    foreign = t.csrf_token(other)
    response = mine.post(URL, headers={"Origin": PUBLIC_ORIGIN, CSRF_HEADER: foreign}, **BASE)
    assert response.status_code == 403


def test_without_a_session_the_answer_is_401_not_a_csrf_hint() -> None:
    t = make_test_app()
    response = t.browser().post(URL, headers={"Origin": "https://evil.example"}, **BASE)
    assert response.status_code == 401


def test_safe_methods_need_no_csrf_token() -> None:
    _t, client, _ = signed_in()
    assert client.get("/api/v1/session", **BASE).status_code == 200


def test_no_cors_headers_are_ever_sent() -> None:
    _t, client, _ = signed_in()
    response = client.get("/api/v1/session", headers={"Origin": "https://evil.example"}, **BASE)
    assert not [k for k, _ in response.headers if k.lower().startswith("access-control-")]
