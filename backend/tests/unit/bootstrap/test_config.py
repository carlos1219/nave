from datetime import timedelta
from pathlib import Path

import pytest

from nave.bootstrap.config import ConfigurationError, Settings, read_pem_file, read_secret_file
from tests.fakes.github import generate_key, to_pem

SECRETS = {
    "/run/secrets/db": "db-pw",
    "/run/secrets/redis": "p@ss/w:rd#1",
    "/run/secrets/maintenance": "maint-pw",
    "/run/secrets/fingerprint": "ab" * 32,
    "/run/secrets/bad-fingerprint": "not-hex",
    "/run/secrets/runner": "cd" * 32,
}


def read(path: Path) -> str:
    return SECRETS[str(path)]


def env(**overrides: str | None) -> dict[str, str]:
    base: dict[str, str | None] = {
        "NAVE_DATABASE_URL": "postgresql+psycopg://nave_app@postgres:5432/nave",
        "NAVE_DATABASE_PASSWORD_FILE": "/run/secrets/db",
        "NAVE_REDIS_URL": "redis://redis:6379/0",
        "NAVE_REDIS_PASSWORD_FILE": "/run/secrets/redis",
        "NAVE_FINGERPRINT_KEY_FILE": "/run/secrets/fingerprint",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def load(**overrides: str | None) -> Settings:
    return Settings.from_environ(env(**overrides), read_secret=read)


def test_defaults_are_complete_and_consistent() -> None:
    s = load()
    assert s.database_url.password == "db-pw"
    assert s.database_url.username == "nave_app"
    assert s.celery.broker_url == "redis://default:p%40ss%2Fw%3Ard%231@redis:6379/0"
    # 300 s de fetch + 2 escáneres de 600 s + margen
    assert s.celery.scan.soft == timedelta(seconds=300 + 1200 + 120)
    assert s.celery.scan.hard == s.celery.scan.soft + timedelta(minutes=1)


def test_repr_hides_every_secret() -> None:
    text = repr(load())
    assert "db-pw" not in text
    assert "p%40ss" not in text
    assert "p@ss" not in text
    assert "abab" not in text


def test_ipv6_redis_hosts_keep_their_brackets() -> None:
    assert load(NAVE_REDIS_URL="redis://[::1]:6379/0").celery.broker_url.endswith("@[::1]:6379/0")


def test_the_redis_user_can_be_named() -> None:
    s = load(NAVE_REDIS_URL="rediss://nave_worker@redis:6380/2")
    assert s.celery.broker_url.startswith("rediss://nave_worker:")
    assert s.celery.broker_url.endswith("@redis:6380/2")


@pytest.mark.parametrize(
    "overrides",
    [
        {"NAVE_DATABASE_URL": None},
        {"NAVE_REDIS_URL": None},
        {"NAVE_DATABASE_PASSWORD_FILE": None},
        {"NAVE_REDIS_PASSWORD_FILE": None},
        {"NAVE_FINGERPRINT_KEY_FILE": None},
        {"NAVE_FINGERPRINT_KEY_FILE": "/run/secrets/bad-fingerprint"},
        {"NAVE_DATABASE_URL": "postgresql+psycopg://nave_app:inline@postgres/nave"},
        {"NAVE_DATABASE_URL": "postgresql+psycopg://postgres/nave"},
        {"NAVE_DATABASE_URL": "not a url"},
        {"NAVE_REDIS_URL": "redis://u:inline@redis:6379/0"},
        {"NAVE_REDIS_URL": "http://redis/0"},
        {"NAVE_REDIS_URL": "redis://redis:99999/0"},
        {"NAVE_REDIS_URL": "redis://redis/0?ssl_cert_reqs=none"},
        {"NAVE_REDIS_URL": "redis://redis/0#frag"},
    ],
)
def test_incomplete_or_unsafe_connection_settings_are_refused(
    overrides: dict[str, str | None],
) -> None:
    with pytest.raises(ConfigurationError) as raised:
        load(**overrides)
    assert "inline" not in str(raised.value)


def test_a_misspelt_variable_is_an_error_not_a_silent_default() -> None:
    with pytest.raises(ConfigurationError, match="NAVE_SCAN_TIMEOUT_SECOND"):
        load(NAVE_SCAN_TIMEOUT_SECOND="5")


def test_variables_of_other_programs_are_ignored() -> None:
    assert load(PATH="/usr/bin", HOME="/root").log_level == "INFO"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NAVE_SCAN_TIMEOUT_SECONDS", "0"),
        ("NAVE_SCAN_TIMEOUT_SECONDS", "-1"),
        ("NAVE_SCAN_TIMEOUT_SECONDS", "9999999"),
        ("NAVE_SCAN_TIMEOUT_SECONDS", "10s"),
        ("NAVE_SCAN_TIMEOUT_SECONDS", "\N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH DIGIT TWO}"),
        ("NAVE_SCAN_TIMEOUT_SECONDS", " 5"),
        ("NAVE_FETCH_MAX_BYTES", "99999999999999"),
        ("NAVE_MAX_SARIF_FINDINGS", "25001"),
        ("NAVE_TASK_MAX_RETRIES", "21"),
        ("NAVE_LOG_LEVEL", "TRACE"),
        ("NAVE_SCAN_TASK_SOFT_SECONDS", "100"),
    ],
)
def test_numeric_settings_are_strictly_parsed_and_bounded(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        load(**{name: value})


def test_explicit_limits_are_applied() -> None:
    s = load(
        NAVE_SCAN_TIMEOUT_SECONDS="60",
        NAVE_FETCH_TIMEOUT_SECONDS="30",
        NAVE_SCAN_TASK_SOFT_SECONDS="600",
        NAVE_SCAN_TASK_HARD_SECONDS="700",
        NAVE_QUEUE_MAX_DEPTH="50",
    )
    assert s.scan_limits.max_duration == timedelta(seconds=60)
    assert s.fetch_limits.max_duration == timedelta(seconds=30)
    assert (s.celery.scan.soft, s.celery.scan.hard) == (
        timedelta(seconds=600),
        timedelta(seconds=700),
    )
    assert s.celery.max_queue_depth == 50


class TestReadSecretFile:
    def test_strips_one_trailing_newline_only(self, tmp_path: Path) -> None:
        f = tmp_path / "s"
        f.write_bytes(b"abc \n")
        assert read_secret_file(f) == "abc "

    @pytest.mark.parametrize(
        "content", [b"", b"\n", b"a\nb", b"a\x00b", b"\xff\xfe", b"x" * 5000, b"a\tb"]
    )
    def test_rejects_unsafe_content(self, tmp_path: Path, content: bytes) -> None:
        f = tmp_path / "s"
        f.write_bytes(content)
        with pytest.raises(ConfigurationError):
            read_secret_file(f)

    def test_missing_file_does_not_echo_the_path_contents(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError) as raised:
            read_secret_file(tmp_path / "missing")
        assert "missing" not in str(raised.value)


MAINTENANCE = {
    "NAVE_MAINTENANCE_DATABASE_URL": "postgresql+psycopg://nave_maintenance@postgres:5432/nave",
    "NAVE_MAINTENANCE_DATABASE_PASSWORD_FILE": "/run/secrets/maintenance",
}


def test_the_maintenance_credential_is_optional_and_kept_apart() -> None:
    assert load().maintenance_database_url is None
    s = load(**MAINTENANCE)
    assert s.maintenance_database_url is not None
    assert s.maintenance_database_url.username == "nave_maintenance"
    assert s.maintenance_database_url.password == "maint-pw"
    assert s.database_url.username == "nave_app"
    assert "maint-pw" not in repr(s)


def test_retention_and_sweep_defaults_follow_the_adrs() -> None:
    s = load()
    assert s.retention.webhook_deliveries == timedelta(days=30)
    assert s.retention.scans == timedelta(days=365)
    assert s.sweep.running_stuck_after == 2 * s.celery.scan.hard
    assert s.sweep.requeue_after < s.sweep.expire_queued_after


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NAVE_RETENTION_SCANS_DAYS", "10"),
        ("NAVE_RETENTION_WEBHOOK_DELIVERIES_DAYS", "0"),
        ("NAVE_RETENTION_OUTBOX_DAYS", "99999"),
        ("NAVE_SWEEP_RUNNING_STUCK_AFTER_SECONDS", "1000"),
        ("NAVE_SWEEP_REQUEUE_AFTER_SECONDS", "3600"),
    ],
)
def test_unsafe_retention_and_sweep_values_are_refused(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        load(**{name: value})


def test_maintenance_url_with_inline_password_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        load(**{**MAINTENANCE, "NAVE_MAINTENANCE_DATABASE_URL": "postgresql+psycopg://m:pw@h/db"})


RUNNER_ENV = {
    "NAVE_RUNNER_URL": "http://scan-runner:8080",
    "NAVE_RUNNER_HMAC_KEY_FILE": "/run/secrets/runner",
    "NAVE_SEMGREP_VERSION": "1.180.0",
    "NAVE_TRIVY_VERSION": "0.58.0",
}


def test_runner_settings_are_optional_for_services_that_do_not_scan() -> None:
    assert load().runner is None


def test_runner_settings_are_loaded_and_the_key_is_hidden() -> None:
    runner = load(**RUNNER_ENV).runner
    assert runner is not None
    assert (runner.url, runner.semgrep_version, runner.trivy_version) == (
        "http://scan-runner:8080",
        "1.180.0",
        "0.58.0",
    )
    assert runner.key.material == bytes.fromhex("cd" * 32)
    assert "cdcd" not in repr(load(**RUNNER_ENV))


@pytest.mark.parametrize(
    "overrides",
    [
        {"NAVE_RUNNER_URL": "ftp://scan-runner"},
        {"NAVE_RUNNER_URL": "http://user:pw@scan-runner"},
        {"NAVE_RUNNER_URL": "http://scan-runner/path"},
        {"NAVE_RUNNER_HMAC_KEY_FILE": None},
        {"NAVE_RUNNER_HMAC_KEY_FILE": "/run/secrets/bad-fingerprint"},
        {"NAVE_SEMGREP_VERSION": None},
        {"NAVE_TRIVY_VERSION": None},
        {"NAVE_SEMGREP_VERSION": "1.0 beta"},
        {"NAVE_TRIVY_VERSION": "latest;rm"},
    ],
)
def test_invalid_runner_settings_are_refused(overrides: dict[str, str | None]) -> None:
    with pytest.raises(ConfigurationError):
        load(**{**RUNNER_ENV, **overrides})


class TestGithubSettings:
    def pem(self) -> str:
        return to_pem(generate_key())

    def load_github(self, **overrides: str | None) -> Settings:
        pem = self.pem()
        base: dict[str, str | None] = {
            "NAVE_GITHUB_APP_ID": "12345",
            "NAVE_GITHUB_APP_PRIVATE_KEY_FILE": "/run/secrets/app-key",
        }
        base.update(overrides)
        return Settings.from_environ(env(**base), read_secret=read, read_pem=lambda _path: pem)

    def test_absent_without_app_id(self) -> None:
        assert load().github is None

    def test_defaults(self) -> None:
        github = self.load_github().github
        assert github is not None
        assert github.app_id == 12345
        assert github.api_url == "https://api.github.com"
        assert github.sarif_upload is False
        assert github.allowed_accounts == frozenset()
        assert github.egress_proxy_url is None

    def test_repr_hides_key(self) -> None:
        assert "PRIVATE" not in repr(self.load_github()).upper().replace("PRIVATE_KEY", "")

    def test_sarif_switch_is_strict(self) -> None:
        github = self.load_github(NAVE_SARIF_UPLOAD="on").github
        assert github is not None
        assert github.sarif_upload is True
        for bad in ("true", "1", "ON", "yes"):
            with pytest.raises(ConfigurationError):
                self.load_github(NAVE_SARIF_UPLOAD=bad)

    def test_allowed_accounts_are_normalised_and_validated(self) -> None:
        github = self.load_github(NAVE_GITHUB_ALLOWED_ACCOUNTS="Acme, other-org ,").github
        assert github is not None
        assert github.allowed_accounts == frozenset({"acme", "other-org"})
        with pytest.raises(ConfigurationError):
            self.load_github(NAVE_GITHUB_ALLOWED_ACCOUNTS="acme,-bad-")

    @pytest.mark.parametrize("value", ["0", "abc", "-1", "1" * 19, "01"])
    def test_app_id_must_be_a_positive_integer(self, value: str) -> None:
        with pytest.raises(ConfigurationError):
            self.load_github(NAVE_GITHUB_APP_ID=value)

    def test_key_file_is_required_and_must_be_a_key(self) -> None:
        with pytest.raises(ConfigurationError):
            self.load_github(NAVE_GITHUB_APP_PRIVATE_KEY_FILE=None)
        with pytest.raises(ConfigurationError):
            Settings.from_environ(
                env(
                    NAVE_GITHUB_APP_ID="1",
                    NAVE_GITHUB_APP_PRIVATE_KEY_FILE="/k",
                ),
                read_secret=read,
                read_pem=lambda _p: (
                    "-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n"
                ),
            )

    @pytest.mark.parametrize(
        "url", ["http://api.github.com", "https://user:pw@api.github.com", "ftp://x"]
    )
    def test_api_url_must_be_plain_https(self, url: str) -> None:
        with pytest.raises(ConfigurationError):
            self.load_github(NAVE_GITHUB_API_URL=url)

    def test_proxy_may_not_carry_credentials(self) -> None:
        assert self.load_github(NAVE_EGRESS_PROXY_URL="http://proxy:3128").github is not None
        with pytest.raises(ConfigurationError):
            self.load_github(NAVE_EGRESS_PROXY_URL="http://u:p@proxy:3128")


class TestPemFile:
    def test_reads_a_pem_with_newlines(self, tmp_path: Path) -> None:

        path = tmp_path / "k.pem"
        path.write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
        assert read_pem_file(path).startswith("-----BEGIN")

    @pytest.mark.parametrize(
        "content",
        [
            b"not a pem",
            b"-----BEGIN X-----\n\x00\n",
            b"-----BEGIN X-----\n\xc3\xa9",
            b"-----BEGIN" + b"a" * 9000,
        ],
    )
    def test_rejects_non_pem_content(self, tmp_path: Path, content: bytes) -> None:

        path = tmp_path / "k.pem"
        path.write_bytes(content)
        with pytest.raises(ConfigurationError):
            read_pem_file(path)

    def test_missing_file(self, tmp_path: Path) -> None:

        with pytest.raises(ConfigurationError):
            read_pem_file(tmp_path / "missing")
