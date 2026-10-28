"""Denegar por defecto: toda ruta declara cómo se autentica (ADR 0016)."""

from nave.delivery.http.security import AUTH_ATTRIBUTE
from tests.fakes.auth import make_test_app

ALLOWED_PUBLIC = {
    "/healthz",
    "/readyz",
    "/api/v1/auth/login",
    "/api/v1/auth/callback",
}
ALLOWED_HMAC = {"/api/v1/webhooks/github"}


def test_every_route_declares_its_authentication_mode() -> None:
    app = make_test_app().app
    undeclared = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        mode = getattr(app.view_functions[rule.endpoint], AUTH_ATTRIBUTE, None)
        if mode is None:
            undeclared.append(rule.rule)
    assert undeclared == []


def test_only_the_expected_routes_are_public_or_hmac() -> None:
    app = make_test_app().app
    public, hmac_routes = set(), set()
    for rule in app.url_map.iter_rules():
        mode = getattr(app.view_functions[rule.endpoint], AUTH_ATTRIBUTE, None)
        if mode == "public":
            public.add(rule.rule)
        elif mode == "hmac":
            hmac_routes.add(rule.rule)
    assert public == ALLOWED_PUBLIC
    assert hmac_routes == ALLOWED_HMAC


def test_every_session_route_answers_401_without_a_cookie() -> None:
    t = make_test_app()
    client = t.browser()
    for rule in t.app.url_map.iter_rules():
        if getattr(t.app.view_functions[rule.endpoint], AUTH_ATTRIBUTE, None) != "session":
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            url = rule.rule
            response = client.open(url, method=method, base_url="https://nave.test")
            assert response.status_code == 401, (method, url)
            t.clock.advance(minutes=1)  # el limitador de intentos fallidos no es lo que se prueba
