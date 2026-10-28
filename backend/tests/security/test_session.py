"""AM-101: robo, fijación y reutilización de sesión (ADR 0016)."""

from nave.application.services.session_tokens import hash_token
from nave.delivery.http.security import LOGIN_COOKIE, SESSION_COOKIE
from tests.fakes.auth import PUBLIC_ORIGIN, make_test_app

BASE = {"base_url": PUBLIC_ORIGIN}


def cookie_lines(response: object, name: str) -> list[str]:
    headers = response.headers.getlist("Set-Cookie")  # type: ignore[attr-defined]
    return [h for h in headers if h.startswith(f"{name}=")]


def attributes(line: str) -> dict[str, str]:
    parts = [p.strip() for p in line.split(";")]
    return {p.split("=", 1)[0].lower(): (p.split("=", 1)[1] if "=" in p else "") for p in parts[1:]}


def test_the_login_cookie_is_host_prefixed_httponly_secure_lax() -> None:
    t = make_test_app()
    response = t.browser().get("/api/v1/auth/login", **BASE)
    (line,) = cookie_lines(response, LOGIN_COOKIE)
    attrs = attributes(line)
    assert {"httponly", "secure"} <= set(attrs)
    assert attrs["samesite"] == "Lax"
    assert attrs["path"] == "/"
    assert "domain" not in attrs  # requisito de __Host-
    assert attrs["max-age"] == "600"


def test_the_session_cookie_is_host_prefixed_httponly_secure_lax_and_bounded() -> None:
    t = make_test_app()
    response = t.sign_in(t.browser())
    (line,) = cookie_lines(response, SESSION_COOKIE)
    attrs = attributes(line)
    assert {"httponly", "secure"} <= set(attrs)
    assert attrs["samesite"] == "Lax"
    assert attrs["path"] == "/"
    assert "domain" not in attrs
    assert int(attrs["max-age"]) == 12 * 3600
    assert line.startswith("__Host-")


def test_the_server_keeps_only_a_hash_of_the_cookie() -> None:
    t = make_test_app()
    response = t.sign_in(t.browser())
    (line,) = cookie_lines(response, SESSION_COOKIE)
    token = line.split("=", 1)[1].split(";", 1)[0]
    (stored,) = t.store.state.sessions
    assert stored == hash_token(token)
    assert token.encode() not in stored
    assert len(token) >= 43  # 256 bits en base64url


def test_a_session_id_chosen_by_the_attacker_is_never_adopted() -> None:
    t = make_test_app()
    client = t.browser()
    planted = "p" * 43
    client.set_cookie(SESSION_COOKIE, planted, domain="nave.test")
    response = t.sign_in(client)
    (line,) = cookie_lines(response, SESSION_COOKIE)
    token = line.split("=", 1)[1].split(";", 1)[0]
    assert token != planted
    assert hash_token(planted) not in t.store.state.sessions


def test_each_login_gets_a_fresh_session_and_the_old_one_keeps_working_independently() -> None:
    t = make_test_app()
    first, second = t.browser(), t.browser()
    a = t.sign_in(first)
    b = t.sign_in(second)
    assert cookie_lines(a, SESSION_COOKIE)[0] != cookie_lines(b, SESSION_COOKIE)[0]
    assert len(t.store.state.sessions) == 2


def test_logout_invalidates_the_cookie_server_side_even_if_the_browser_keeps_it() -> None:
    t = make_test_app()
    client = t.browser()
    t.sign_in(client)
    stolen = client.get_cookie(SESSION_COOKIE, domain="nave.test")
    assert stolen is not None
    csrf = t.csrf_token(client)
    client.post(
        "/api/v1/auth/logout",
        headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf},
        **BASE,
    )
    thief = t.browser()
    thief.set_cookie(SESSION_COOKIE, stolen.value, domain="nave.test")
    assert thief.get("/api/v1/session", **BASE).status_code == 401


def test_authenticated_responses_are_not_cacheable() -> None:
    t = make_test_app()
    client = t.browser()
    t.sign_in(client)
    response = client.get("/api/v1/session", **BASE)
    assert response.headers["Cache-Control"] == "no-store"


def test_a_login_flow_cannot_be_completed_from_another_browser() -> None:
    """El atacante inicia el flujo y entrega a la víctima su URL de callback (login CSRF)."""
    t = make_test_app()
    attacker, victim = t.browser(), t.browser()
    attacker.get("/api/v1/auth/login", **BASE)
    state = t.kit.oidc.urls[-1]["state"]
    response = victim.get(
        "/api/v1/auth/callback", query_string={"state": state, "code": "good"}, **BASE
    )
    assert response.status_code == 401
    assert t.store.state.sessions == {}
