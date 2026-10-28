"""Flujo de login, sesión y logout por HTTP (ADR 0016)."""

import json

from nave.delivery.http.problems import PROBLEM_JSON
from nave.delivery.http.security import CSRF_HEADER, LOGIN_COOKIE, SESSION_COOKIE
from tests.fakes.auth import PUBLIC_ORIGIN, FakeOidc, make_test_app

BASE = {"base_url": PUBLIC_ORIGIN}


def test_the_full_flow_login_session_logout() -> None:
    t = make_test_app()
    client = t.browser()

    started = client.get("/api/v1/auth/login", **BASE)
    assert started.status_code == 302
    assert started.headers["Location"].startswith("https://idp.test/authorize?state=")

    done = t.sign_in(client)
    assert done.status_code == 302
    assert done.headers["Location"] == "/"

    me = client.get("/api/v1/session", **BASE)
    assert me.status_code == 200
    body = me.get_json()
    assert body["user"]["display_name"] == "Alice"
    assert set(body) == {"user", "csrf_token", "expires_at"}

    out = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": PUBLIC_ORIGIN, CSRF_HEADER: body["csrf_token"]},
        **BASE,
    )
    assert out.status_code == 204
    assert client.get("/api/v1/session", **BASE).status_code == 401


def test_the_callback_never_redirects_where_the_client_asks() -> None:
    t = make_test_app()
    client = t.browser()
    client.get("/api/v1/auth/login", **BASE)
    state = t.kit.oidc.urls[-1]["state"]
    response = client.get(
        "/api/v1/auth/callback",
        query_string={
            "state": state,
            "code": "good",
            "next": "https://evil.example",
            "redirect_uri": "https://evil.example",
        },
        **BASE,
    )
    assert response.headers["Location"] == "/"


def test_failed_callbacks_are_a_uniform_401_and_clear_the_login_cookie() -> None:
    t = make_test_app()
    client = t.browser()
    client.get("/api/v1/auth/login", **BASE)
    state = t.kit.oidc.urls[-1]["state"]
    cases = [
        {"state": state, "code": "bad"},  # el IdP rechaza el código
        {"state": "z" * 43, "code": "good"},  # state desconocido
        {"code": "good"},  # sin state
        {"state": state, "error": "access_denied"},  # el usuario canceló
        {},
    ]
    bodies = []
    for params in cases:
        response = client.get("/api/v1/auth/callback", query_string=params, **BASE)
        assert response.status_code == 401
        assert response.mimetype == PROBLEM_JSON
        assert SESSION_COOKIE not in "".join(response.headers.getlist("Set-Cookie"))
        assert f"{LOGIN_COOKIE}=;" in "".join(response.headers.getlist("Set-Cookie"))
        bodies.append(json.loads(response.get_data()))
    assert all(set(b) == {"type", "title", "status", "request_id"} for b in bodies)
    assert len({(b["type"], b["title"]) for b in bodies}) == 1


def test_repeated_parameters_cannot_smuggle_a_second_state() -> None:
    t = make_test_app()
    client = t.browser()
    client.get("/api/v1/auth/login", **BASE)
    state = t.kit.oidc.urls[-1]["state"]
    response = client.get(
        f"/api/v1/auth/callback?state=z{'z' * 42}&state={state}&code=good", **BASE
    )
    assert response.status_code == 401


def test_idp_outage_is_503_with_retry_after_and_no_detail() -> None:
    t = make_test_app(FakeOidc(down=True))
    response = t.browser().get("/api/v1/auth/login", **BASE)
    assert response.status_code == 503
    assert response.headers["Retry-After"]
    assert "idp" not in response.get_data(as_text=True).lower()


def test_session_requires_a_valid_cookie() -> None:
    t = make_test_app()
    client = t.browser()
    assert client.get("/api/v1/session", **BASE).status_code == 401
    client.set_cookie(SESSION_COOKIE, "f" * 43, domain="nave.test")
    response = client.get("/api/v1/session", **BASE)
    assert response.status_code == 401
    assert response.mimetype == PROBLEM_JSON


def test_the_session_ends_after_the_idle_timeout() -> None:
    t = make_test_app()
    client = t.browser()
    t.sign_in(client)
    t.clock.advance(minutes=31)
    assert client.get("/api/v1/session", **BASE).status_code == 401


def test_logout_is_not_available_by_get() -> None:
    t = make_test_app()
    client = t.browser()
    t.sign_in(client)
    assert client.get("/api/v1/auth/logout", **BASE).status_code == 405
    assert client.get("/api/v1/session", **BASE).status_code == 200
