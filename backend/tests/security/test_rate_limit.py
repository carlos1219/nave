"""AM-815: el abuso se frena sin que un usuario o una IP arrastre a otros."""

from flask.testing import FlaskClient

from nave.domain.value_objects.role import Role
from tests.fakes.auth import PUBLIC_ORIGIN, make_test_app

LOGIN = "/api/v1/auth/login"
SESSION = "/api/v1/session"


def hits(client: FlaskClient, path: str, n: int, **kw: str) -> list[int]:
    return [client.get(path, base_url=PUBLIC_ORIGIN, **kw).status_code for _ in range(n)]


def test_login_is_limited_per_client_and_answers_429_with_retry_after() -> None:
    t = make_test_app()
    client = t.browser()
    codes = hits(client, LOGIN, 12)
    assert codes[:10] == [302] * 10
    assert codes[10:] == [429, 429]
    blocked = client.get(LOGIN, base_url=PUBLIC_ORIGIN)
    assert blocked.headers["Retry-After"].isdigit()
    assert blocked.mimetype == "application/problem+json"
    assert blocked.get_json()["type"] == "urn:nave:problem:too-many-requests"
    t.clock.advance(minutes=1)
    assert client.get(LOGIN, base_url=PUBLIC_ORIGIN).status_code == 302


def test_a_different_client_address_is_not_affected() -> None:
    t = make_test_app()
    client = t.browser()
    hits(client, LOGIN, 12)
    other = client.get(LOGIN, base_url=PUBLIC_ORIGIN, environ_overrides={"REMOTE_ADDR": "10.9.9.9"})
    assert other.status_code == 302


def test_invalid_cookies_never_throttle_valid_users_sharing_the_address() -> None:
    t = make_test_app()
    api = t.sign_in_as(Role.VIEWER)
    attacker = t.browser()
    attacker.set_cookie("__Host-nave_session", "y" * 43, domain="nave.test")
    assert set(hits(attacker, SESSION, 30)) == {401}
    assert api.client.get(SESSION, base_url=PUBLIC_ORIGIN).status_code == 200


def test_reads_are_limited_per_user_not_per_address() -> None:
    t = make_test_app()
    api = t.sign_in_as(Role.VIEWER)
    codes = hits(api.client, SESSION, 70)
    # La fixture ya gastó una ficha de lectura al pedir el token CSRF.
    assert codes == [200] * 59 + [429] * 11
    t.clock.advance(minutes=1)
    assert api.client.get(SESSION, base_url=PUBLIC_ORIGIN).status_code == 200


def test_writes_have_a_tighter_bucket_than_reads() -> None:
    t = make_test_app()
    api = t.sign_in_as(Role.TRIAGER)
    missing = "/scans/00000000-0000-4000-8000-0000000000aa/cancel"
    codes = [api.call("POST", missing).status_code for _ in range(12)]
    assert codes == [404] * 10 + [429] * 2
    assert api.get("/session").status_code == 200  # las lecturas tienen su propio cubo


def test_a_rejected_request_still_carries_the_hardening_headers() -> None:
    t = make_test_app()
    client = t.browser()
    hits(client, LOGIN, 11)
    blocked = client.get(LOGIN, base_url=PUBLIC_ORIGIN)
    assert blocked.status_code == 429
    assert blocked.headers["Cache-Control"] == "no-store"
    assert blocked.headers["X-Request-Id"]


def test_forwarded_for_is_ignored_without_trusted_proxies() -> None:
    t = make_test_app()
    client = t.browser()
    for n in range(12):  # cada petición finge una IP distinta
        client.get(LOGIN, base_url=PUBLIC_ORIGIN, headers={"X-Forwarded-For": f"203.0.113.{n}"})
    blocked = client.get(LOGIN, base_url=PUBLIC_ORIGIN, headers={"X-Forwarded-For": "198.51.100.1"})
    assert blocked.status_code == 429


def test_forwarded_for_is_honoured_for_the_declared_number_of_hops() -> None:
    t = make_test_app(trusted_proxy_hops=1)
    client = t.browser()
    for _ in range(12):
        client.get(LOGIN, base_url=PUBLIC_ORIGIN, headers={"X-Forwarded-For": "203.0.113.7"})
    same = client.get(LOGIN, base_url=PUBLIC_ORIGIN, headers={"X-Forwarded-For": "203.0.113.7"})
    other = client.get(LOGIN, base_url=PUBLIC_ORIGIN, headers={"X-Forwarded-For": "203.0.113.8"})
    assert (same.status_code, other.status_code) == (429, 302)


def test_a_client_cannot_forge_the_address_the_proxy_appended() -> None:
    # Con un salto de confianza solo cuenta la última entrada, la que añade el proxy.
    t = make_test_app(trusted_proxy_hops=1)
    client = t.browser()
    for n in range(12):
        client.get(
            LOGIN,
            base_url=PUBLIC_ORIGIN,
            headers={"X-Forwarded-For": f"10.0.0.{n}, 203.0.113.7"},
        )
    blocked = client.get(
        LOGIN, base_url=PUBLIC_ORIGIN, headers={"X-Forwarded-For": "10.9.9.9, 203.0.113.7"}
    )
    assert blocked.status_code == 429
