from pathlib import Path

from nave.bootstrap.api import build_api
from nave.bootstrap.api_config import ApiSettings


def test_the_api_container_serves_health_and_the_webhook_route(tmp_path: Path) -> None:
    files = {}
    for name, content in (("db", "pw"), ("redis", "pw"), ("hook", "h" * 40), ("oidc", "o")):
        path = tmp_path / name
        path.write_text(content)
        files[name] = str(path)
    settings = ApiSettings.from_environ(
        {
            "NAVE_DATABASE_URL": "postgresql+psycopg://nave_app@postgres:5432/nave",
            "NAVE_DATABASE_PASSWORD_FILE": files["db"],
            "NAVE_REDIS_URL": "redis://redis:6379/0",
            "NAVE_REDIS_PASSWORD_FILE": files["redis"],
            "NAVE_GITHUB_WEBHOOK_SECRET_FILE": files["hook"],
            "NAVE_PUBLIC_URL": "https://nave.example.com",
            "NAVE_OIDC_ISSUER": "https://idp.example.com",
            "NAVE_OIDC_CLIENT_ID": "nave",
            "NAVE_OIDC_CLIENT_SECRET_FILE": files["oidc"],
        }
    )
    container = build_api(settings)
    try:
        client = container.app.test_client()
        assert client.get("/healthz").status_code == 200
        # Sin firma: llega al controlador (no 404) y se rechaza antes de tocar nada.
        assert client.post("/api/v1/webhooks/github", data=b"{}").status_code == 401
        rules = {r.rule for r in container.app.url_map.iter_rules()}
        assert "/api/v1/webhooks/github" in rules
        assert {"/api/v1/auth/login", "/api/v1/auth/callback", "/api/v1/session"} <= rules
        # Sin cookie de sesión: 401, nunca datos.
        assert client.get("/api/v1/session").status_code == 401
        assert "/static/<path:filename>" not in rules
    finally:
        container.close()
