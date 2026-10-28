from pathlib import Path

import pytest

from nave.bootstrap.api_config import ApiSettings
from nave.bootstrap.config import ConfigurationError, Settings

SECRETS = {
    "/s/db": "db-pw",
    "/s/redis": "redis-pw",
    "/s/hook": "w" * 40,
    "/s/hook-old": "o" * 40,
    "/s/short": "short",
    "/s/oidc": "oidc-secret",
}


def read(path: Path) -> str:
    return SECRETS[str(path)]


def env(**overrides: str | None) -> dict[str, str]:
    base: dict[str, str | None] = {
        "NAVE_DATABASE_URL": "postgresql+psycopg://nave_app@postgres:5432/nave",
        "NAVE_DATABASE_PASSWORD_FILE": "/s/db",
        "NAVE_REDIS_URL": "redis://redis:6379/0",
        "NAVE_REDIS_PASSWORD_FILE": "/s/redis",
        "NAVE_GITHUB_WEBHOOK_SECRET_FILE": "/s/hook",
        "NAVE_PUBLIC_URL": "https://nave.example.com",
        "NAVE_OIDC_ISSUER": "https://idp.example.com/realms/nave",
        "NAVE_OIDC_CLIENT_ID": "nave",
        "NAVE_OIDC_CLIENT_SECRET_FILE": "/s/oidc",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def load(**overrides: str | None) -> ApiSettings:
    return ApiSettings.from_environ(env(**overrides), read_secret=read)


def test_defaults() -> None:
    s = load()
    assert s.webhook_secrets == (b"w" * 40,)
    assert s.scan_fork_pull_requests is False
    assert s.database_url.password == "db-pw"
    assert s.log_level == "INFO"


def test_repr_hides_secrets() -> None:
    text = repr(load())
    for secret in ("db-pw", "redis-pw", "w" * 40, "oidc-secret"):
        assert secret not in text


def test_rotation_keeps_both_secrets() -> None:
    s = load(NAVE_GITHUB_WEBHOOK_SECRET_PREVIOUS_FILE="/s/hook-old")
    assert s.webhook_secrets == (b"w" * 40, b"o" * 40)


def test_previous_secret_must_differ() -> None:
    with pytest.raises(ConfigurationError, match="differ"):
        load(NAVE_GITHUB_WEBHOOK_SECRET_PREVIOUS_FILE="/s/hook")


def test_short_secrets_are_refused() -> None:
    SECRETS["/s/short"] = "short"
    with pytest.raises(ConfigurationError, match="at least"):
        load(NAVE_GITHUB_WEBHOOK_SECRET_FILE="/s/short")


def test_webhook_secret_is_required() -> None:
    with pytest.raises(ConfigurationError, match="NAVE_GITHUB_WEBHOOK_SECRET_FILE"):
        load(NAVE_GITHUB_WEBHOOK_SECRET_FILE=None)


def test_fork_policy_is_an_explicit_switch() -> None:
    assert load(NAVE_SCAN_FORK_PULL_REQUESTS="on").scan_fork_pull_requests is True
    with pytest.raises(ConfigurationError):
        load(NAVE_SCAN_FORK_PULL_REQUESTS="yes")


@pytest.mark.parametrize(
    "name",
    [
        "NAVE_FINGERPRINT_KEY_FILE",
        "NAVE_GITHUB_APP_PRIVATE_KEY_FILE",
        "NAVE_GITHUB_APP_ID",
        "NAVE_RUNNER_HMAC_KEY_FILE",
        "NAVE_MAINTENANCE_DATABASE_URL",
        "NAVE_MAINTENANCE_DATABASE_PASSWORD_FILE",
        "NAVE_SARIF_UPLOAD",
        "NAVE_EGRESS_PROXY_URL",
    ],
)
def test_the_api_refuses_to_start_with_worker_secrets(name: str) -> None:
    with pytest.raises(ConfigurationError, match="another service"):
        load(**{name: "/s/anything"})


def test_unknown_variables_are_errors() -> None:
    with pytest.raises(ConfigurationError, match="unknown"):
        load(NAVE_TYPO="1")


@pytest.mark.parametrize(
    "name",
    [
        "NAVE_GITHUB_WEBHOOK_SECRET_FILE",
        "NAVE_GITHUB_WEBHOOK_SECRET_PREVIOUS_FILE",
        "NAVE_SCAN_FORK_PULL_REQUESTS",
        "NAVE_PUBLIC_URL",
        "NAVE_OIDC_ISSUER",
        "NAVE_OIDC_CLIENT_ID",
        "NAVE_OIDC_CLIENT_SECRET_FILE",
        "NAVE_SESSION_ABSOLUTE_HOURS",
        "NAVE_SESSION_IDLE_MINUTES",
        "NAVE_DEV_INSECURE_HTTP",
        "NAVE_TRUSTED_PROXY_HOPS",
        "NAVE_OIDC_PROXY_URL",
    ],
)
def test_the_worker_refuses_the_webhook_secret(name: str) -> None:
    environ = env(
        NAVE_FINGERPRINT_KEY_FILE="/s/db",
        **{name: "/s/hook"},
    )
    with pytest.raises(ConfigurationError, match="another service"):
        Settings.from_environ(environ, read_secret=lambda _p: "ab" * 32)


class TestLoginSettings:
    def test_defaults(self) -> None:
        s = load()
        assert s.public_origin == "https://nave.example.com"
        assert s.oidc.redirect_uri == "https://nave.example.com/api/v1/auth/callback"
        assert s.session.absolute.total_seconds() == 12 * 3600
        assert s.session.idle.total_seconds() == 30 * 60

    @pytest.mark.parametrize(
        "url",
        [
            "http://nave.example.com",
            "https://nave.example.com/app",
            "https://nave.example.com/?x=1",
            "https://user:pw@nave.example.com",
            "nave.example.com",
        ],
    )
    def test_public_url_must_be_a_plain_https_origin(self, url: str) -> None:
        with pytest.raises(ConfigurationError, match="NAVE_PUBLIC_URL"):
            load(NAVE_PUBLIC_URL=url)

    def test_default_port_is_dropped_and_other_ports_kept(self) -> None:
        assert load(NAVE_PUBLIC_URL="https://nave.example.com:443").public_origin == (
            "https://nave.example.com"
        )
        assert load(NAVE_PUBLIC_URL="https://nave.example.com:8443").public_origin == (
            "https://nave.example.com:8443"
        )

    @pytest.mark.parametrize(
        "issuer", ["http://idp.example.com", "idp", "https://a@idp.example.com"]
    )
    def test_issuer_must_be_https(self, issuer: str) -> None:
        with pytest.raises(ConfigurationError, match="NAVE_OIDC_ISSUER"):
            load(NAVE_OIDC_ISSUER=issuer)

    def test_plain_http_is_only_for_loopback_and_only_with_the_dev_switch(self) -> None:
        dev = {
            "NAVE_DEV_INSECURE_HTTP": "on",
            "NAVE_PUBLIC_URL": "http://localhost:8080",
            "NAVE_OIDC_ISSUER": "http://localhost:8081/realms/nave",
        }
        s = load(**dev)
        assert s.public_origin == "http://localhost:8080"
        assert s.oidc.allow_insecure_loopback is True
        with pytest.raises(ConfigurationError):
            load(**{**dev, "NAVE_DEV_INSECURE_HTTP": None})
        with pytest.raises(ConfigurationError):
            load(**{**dev, "NAVE_PUBLIC_URL": "http://nave.example.com"})

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("NAVE_SESSION_ABSOLUTE_HOURS", "0"),
            ("NAVE_SESSION_ABSOLUTE_HOURS", "100"),
            ("NAVE_SESSION_IDLE_MINUTES", "1"),
            ("NAVE_SESSION_IDLE_MINUTES", "x"),
        ],
    )
    def test_session_limits_are_bounded(self, name: str, value: str) -> None:
        with pytest.raises(ConfigurationError):
            load(**{name: value})

    def test_idle_cannot_exceed_the_absolute_lifetime(self) -> None:
        with pytest.raises(ConfigurationError):
            load(NAVE_SESSION_ABSOLUTE_HOURS="1", NAVE_SESSION_IDLE_MINUTES="120")


class TestTrustedProxyHops:
    def test_default_is_no_trusted_proxy(self) -> None:
        assert load().trusted_proxy_hops == 0

    def test_accepts_a_small_number(self) -> None:
        assert load(NAVE_TRUSTED_PROXY_HOPS="2").trusted_proxy_hops == 2

    @pytest.mark.parametrize("value", ["-1", "4", "x", "1.5"])
    def test_rejects_anything_else(self, value: str) -> None:
        with pytest.raises(ConfigurationError):
            load(NAVE_TRUSTED_PROXY_HOPS=value)


class TestOidcProxy:
    def test_default_is_direct(self) -> None:
        assert load().oidc.proxy_url is None

    def test_accepts_an_http_proxy(self) -> None:
        assert (
            load(NAVE_OIDC_PROXY_URL="http://egress.internal:3128").oidc.proxy_url
            == "http://egress.internal:3128"
        )

    @pytest.mark.parametrize(
        "url", ["https://egress.internal:3128", "http://u:p@egress:3128", "egress:3128"]
    )
    def test_rejects_anything_else(self, url: str) -> None:
        with pytest.raises(ConfigurationError, match="NAVE_OIDC_PROXY_URL"):
            load(NAVE_OIDC_PROXY_URL=url)
