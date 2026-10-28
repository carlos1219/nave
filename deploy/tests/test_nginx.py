"""Nginx (CLAUDE.md §15, ADR 0022): cabeceras en cada location, IP real, límites y registros."""

import re

import pytest


@pytest.fixture(scope="module")
def conf(root) -> str:
    return (root / "deploy/nginx/nginx.conf").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def headers(root) -> str:
    return (root / "deploy/nginx/snippets/security-headers.conf").read_text(
        encoding="utf-8"
    )


def blocks(text: str, opener: str) -> list[tuple[str, str]]:
    found = []
    for match in re.finditer(opener + r"\s*\{", text):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        found.append((match.group(0), text[match.end() : i - 1]))
    return found


def test_cada_location_con_cabeceras_o_proxy_incluye_el_fragmento(conf):
    locations = blocks(conf, r"location\s+[^{]+")
    assert len(locations) >= 5
    for head, body in locations:
        if "return 308" in body:
            continue  # redirección a HTTPS
        assert "snippets/security-headers.conf" in body, head


def test_cabeceras_obligatorias(headers):
    for header in (
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "X-Frame-Options",
        "Cross-Origin-Opener-Policy",
        "Cross-Origin-Resource-Policy",
    ):
        assert f"add_header {header}" in headers, header
    assert "snippets/hsts.conf" in headers
    assert all(
        line.endswith("always;")
        for line in headers.splitlines()
        if line.startswith("add_header")
    )


def test_csp_estricta(headers):
    csp = re.search(r'Content-Security-Policy "([^"]+)"', headers)
    assert csp is not None
    policy = csp.group(1)
    for bad in ("unsafe-inline", "unsafe-eval", "*", "data:", "http:"):
        assert bad not in policy, bad
    for needed in (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
        # F7-23 / AM-701: el navegador rechaza innerHTML y similares sin política de Trusted
        # Types; la aplicación no usa ninguno (comprobado en Chrome con todas las páginas).
        "require-trusted-types-for 'script'",
    ):
        assert needed in policy, needed


def test_hsts_solo_en_el_fragmento_de_produccion(root):
    prod = (root / "deploy/nginx/snippets/hsts-prod.conf").read_text(encoding="utf-8")
    dev = (root / "deploy/nginx/snippets/hsts-dev.conf").read_text(encoding="utf-8")
    assert "Strict-Transport-Security" in prod and "max-age=31536000" in prod
    assert "add_header" not in dev


def test_ip_del_cliente_no_falseable(root):
    proxy = (root / "deploy/nginx/snippets/proxy-api.conf").read_text(encoding="utf-8")
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in proxy
    assert "$proxy_add_x_forwarded_for" not in proxy
    assert 'proxy_set_header Forwarded "";' in proxy
    assert "set $api_upstream http://api:8000;" in proxy
    assert "proxy_pass $api_upstream;" in proxy
    assert "resolver 127.0.0.11" in (root / "deploy/nginx/nginx.conf").read_text(encoding="utf-8")


def test_registros_sin_consulta(conf):
    fmt = re.search(r"log_format nave (.*?);", conf, re.DOTALL)
    assert fmt is not None
    assert "$request_uri" not in fmt.group(1) and "$request " not in fmt.group(1)
    assert "$args" not in conf and "$query_string" not in conf


def test_tls_y_exposicion(conf):
    assert "server_tokens off;" in conf
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in conf
    assert "ssl_session_tickets off;" in conf
    assert "/run/secrets/tls_cert" in conf and "/run/secrets/tls_key" in conf
    assert "Access-Control-Allow-Origin" not in conf
    assert "autoindex" not in conf


def test_limites(conf, root):
    api = dict(blocks(conf, r"location\s+/api/"))
    assert any("limit_req zone=api" in body for body in api.values())
    webhook = [body for head, body in blocks(conf, r"location\s+/api/v1/webhooks/")]
    assert "limit_req zone=webhook" in webhook[0]
    # El cuerpo máximo del webhook no puede superar al de la API (la API rechaza lo demás).
    code = (root / "backend/src/nave/delivery/http/webhook.py").read_text(
        encoding="utf-8"
    )
    mib = int(
        re.search(r"MAX_BODY_BYTES: Final = (\d+) \* 1024 \* 1024", code).group(1)
    )
    assert f"client_max_body_size {mib}m;" in webhook[0]
    assert re.search(r"^\s*client_max_body_size 1m;", conf, re.MULTILINE)


def test_spa_solo_get_y_sin_cache_del_indice(conf):
    assert "try_files $uri /index.html;" in conf
    assert '"no-cache"' in conf and "immutable" in conf
    assert "return 405" in conf


def test_redireccion_http_valida_el_host(conf):
    assert "$host_valid" in conf and "return 421" in conf
