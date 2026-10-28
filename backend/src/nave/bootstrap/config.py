"""Configuración tipada desde el entorno y Docker secrets (CLAUDE.md §12, AM-306).

- Los secretos solo se aceptan como ruta a fichero (`*_FILE`); una contraseña en una
  variable de entorno o dentro de una URL se rechaza.
- Una variable `NAVE_*` desconocida es un error: un typo en un límite de seguridad no
  puede pasar por "valor por defecto".
- Los mensajes de error nombran la variable, nunca su valor.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Final
from urllib.parse import quote, urlsplit

from sqlalchemy import URL
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from nave.application.dto.maintenance import RetentionPolicy, SweepThresholds
from nave.application.dto.scanning import FetchLimits, ScanLimits
from nave.application.services.fingerprinting import FingerprintKey
from nave.domain.errors import DomainError
from nave.domain.value_objects.repo_ref import is_valid_account_login
from nave.infrastructure.queue.retry import RetryPolicy
from nave.infrastructure.queue.settings import CelerySettings, TaskTimeLimits
from nave.infrastructure.sandbox.protocol import RunnerKey
from nave.infrastructure.sandbox.runner_client import HttpTransport
from nave.infrastructure.vcs.app_jwt import AppPrivateKey
from nave.infrastructure.vcs.github_http import DEFAULT_BASE_URL, HttpsGithubTransport

_PREFIX: Final = "NAVE_"
_MAX_SECRET_BYTES: Final = 4096
_INT: Final = re.compile(r"[0-9]{1,12}", re.ASCII)
_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
_EXPECTED_SCANNERS: Final = 2
# No puede superar el máximo por defecto del runner (`NAVE_RUNNER_MAX_OUTPUT_BYTES`): el
# runner rechaza con 400 cualquier petición de escaneo por encima (test cruzado en e2e).
DEFAULT_SCANNER_MAX_OUTPUT_BYTES: Final = 64 << 20
_SCANNER_VERSION: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", re.ASCII)
_TASK_MARGIN: Final = timedelta(minutes=2)
_KILL_GRACE: Final = timedelta(minutes=1)
_MAX_PEM_BYTES: Final = 8192
_PEM_HEADER: Final = "-----BEGIN"
_APP_ID: Final = re.compile(r"[1-9][0-9]{0,17}", re.ASCII)
_MAX_ALLOWED_ACCOUNTS: Final = 100

KNOWN_VARIABLES: Final = frozenset(
    {
        "NAVE_DATABASE_URL",
        "NAVE_DATABASE_PASSWORD_FILE",
        "NAVE_MAINTENANCE_DATABASE_URL",
        "NAVE_MAINTENANCE_DATABASE_PASSWORD_FILE",
        "NAVE_RETENTION_WEBHOOK_DELIVERIES_DAYS",
        "NAVE_RETENTION_SCAN_LOGS_DAYS",
        "NAVE_RETENTION_OUTBOX_DAYS",
        "NAVE_RETENTION_SCANS_DAYS",
        "NAVE_SWEEP_REQUEUE_AFTER_SECONDS",
        "NAVE_SWEEP_QUEUE_EXPIRE_AFTER_SECONDS",
        "NAVE_SWEEP_RUNNING_STUCK_AFTER_SECONDS",
        "NAVE_FINGERPRINT_KEY_FILE",
        "NAVE_RUNNER_URL",
        "NAVE_RUNNER_HMAC_KEY_FILE",
        "NAVE_SEMGREP_VERSION",
        "NAVE_TRIVY_VERSION",
        "NAVE_REDIS_URL",
        "NAVE_REDIS_PASSWORD_FILE",
        "NAVE_LOG_LEVEL",
        "NAVE_FETCH_TIMEOUT_SECONDS",
        "NAVE_FETCH_MAX_BYTES",
        "NAVE_FETCH_MAX_FILES",
        "NAVE_SCAN_TIMEOUT_SECONDS",
        "NAVE_SCANNER_MAX_OUTPUT_BYTES",
        "NAVE_SCANNER_MAX_RESULTS",
        "NAVE_MAX_SARIF_FINDINGS",
        "NAVE_SCAN_TASK_SOFT_SECONDS",
        "NAVE_SCAN_TASK_HARD_SECONDS",
        "NAVE_QUEUE_MAX_DEPTH",
        "NAVE_TASK_MAX_RETRIES",
        "NAVE_WORKER_MAX_TASKS_PER_CHILD",
        "NAVE_GITHUB_APP_ID",
        "NAVE_GITHUB_APP_PRIVATE_KEY_FILE",
        "NAVE_GITHUB_API_URL",
        "NAVE_EGRESS_PROXY_URL",
        "NAVE_SARIF_UPLOAD",
        "NAVE_GITHUB_ALLOWED_ACCOUNTS",
        "NAVE_GITHUB_WEBHOOK_SECRET_FILE",
        "NAVE_GITHUB_WEBHOOK_SECRET_PREVIOUS_FILE",
        "NAVE_SCAN_FORK_PULL_REQUESTS",
        "NAVE_PUBLIC_URL",
        "NAVE_OIDC_ISSUER",
        "NAVE_OIDC_CLIENT_ID",
        "NAVE_OIDC_CLIENT_SECRET_FILE",
        "NAVE_OIDC_PROXY_URL",
        "NAVE_SESSION_ABSOLUTE_HOURS",
        "NAVE_SESSION_IDLE_MINUTES",
        "NAVE_DEV_INSECURE_HTTP",
        "NAVE_TRUSTED_PROXY_HOPS",
    }
)

# Cada secreto vive en un solo servicio (A-01, A-02, AM-803, AM-805): encontrar uno en el
# entorno equivocado es un error de despliegue que debe impedir el arranque.
API_ONLY_VARIABLES: Final = frozenset(
    {
        "NAVE_GITHUB_WEBHOOK_SECRET_FILE",
        "NAVE_GITHUB_WEBHOOK_SECRET_PREVIOUS_FILE",
        "NAVE_SCAN_FORK_PULL_REQUESTS",
        "NAVE_PUBLIC_URL",
        "NAVE_OIDC_ISSUER",
        "NAVE_OIDC_CLIENT_ID",
        "NAVE_OIDC_CLIENT_SECRET_FILE",
        "NAVE_OIDC_PROXY_URL",
        "NAVE_SESSION_ABSOLUTE_HOURS",
        "NAVE_SESSION_IDLE_MINUTES",
        "NAVE_DEV_INSECURE_HTTP",
        "NAVE_TRUSTED_PROXY_HOPS",
    }
)
WORKER_ONLY_VARIABLES: Final = frozenset(
    {
        "NAVE_MAINTENANCE_DATABASE_URL",
        "NAVE_MAINTENANCE_DATABASE_PASSWORD_FILE",
        "NAVE_FINGERPRINT_KEY_FILE",
        "NAVE_RUNNER_URL",
        "NAVE_RUNNER_HMAC_KEY_FILE",
        "NAVE_SEMGREP_VERSION",
        "NAVE_TRIVY_VERSION",
        "NAVE_GITHUB_APP_ID",
        "NAVE_GITHUB_APP_PRIVATE_KEY_FILE",
        "NAVE_GITHUB_API_URL",
        "NAVE_EGRESS_PROXY_URL",
        "NAVE_SARIF_UPLOAD",
        "NAVE_GITHUB_ALLOWED_ACCOUNTS",
    }
)


class ConfigurationError(Exception):
    """Configuración inválida o incompleta. Impide arrancar; nunca se degrada en silencio."""


def read_secret_file(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_SECRET_BYTES + 1)
    except OSError as error:
        raise ConfigurationError(f"cannot read secret file ({type(error).__name__})") from error
    if len(raw) > _MAX_SECRET_BYTES:
        raise ConfigurationError("secret file is too large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigurationError("secret file is not UTF-8") from error
    text = text.removesuffix("\n").removesuffix("\r")
    if not text or any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):  # noqa: PLR2004
        raise ConfigurationError("secret file is empty or has control characters")
    return text


def read_pem_file(path: Path) -> str:
    """Clave privada de la GitHub App: un PEM tiene saltos de línea, que `read_secret_file`
    rechaza. Aquí solo se admite ASCII imprimible y saltos de línea, con tope de tamaño."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_PEM_BYTES + 1)
    except OSError as error:
        raise ConfigurationError(f"cannot read key file ({type(error).__name__})") from error
    if len(raw) > _MAX_PEM_BYTES:
        raise ConfigurationError("key file is too large")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise ConfigurationError("key file is not ASCII") from error
    if not text.startswith(_PEM_HEADER) or any(
        (ord(c) < 0x20 and c not in "\r\n") or ord(c) == 0x7F  # noqa: PLR2004
        for c in text
    ):
        raise ConfigurationError("key file is not a PEM document")
    return text


@dataclass(frozen=True, slots=True)
class GithubSettings:
    """Integración con GitHub: solo el worker la tiene (AM-803)."""

    app_id: int
    private_key: AppPrivateKey = field(repr=False)
    api_url: str
    egress_proxy_url: str | None
    sarif_upload: bool
    allowed_accounts: frozenset[str]
    """Cuentas (en minúsculas) cuyas instalaciones se registran. Vacío: ninguna (ADR 0014)."""


@dataclass(frozen=True, slots=True)
class RunnerClientSettings:
    """Cómo habla el worker con el `scan-runner` (ADR 0012). Las versiones de los
    escáneres son las que el worker acepta: un runner que informe otra se rechaza."""

    url: str
    key: RunnerKey = field(repr=False)
    semgrep_version: str
    trivy_version: str


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: URL = field(repr=False)
    celery: CelerySettings
    fetch_limits: FetchLimits
    scan_limits: ScanLimits
    max_findings_per_scanner: int
    max_sarif_findings: int
    fingerprint_key: FingerprintKey = field(repr=False)
    log_level: str
    retention: RetentionPolicy
    sweep: SweepThresholds
    maintenance_database_url: URL | None = field(default=None, repr=False)
    """Credencial de `nave_maintenance`. Solo la necesita el worker de la cola `maintenance`."""
    runner: RunnerClientSettings | None = None
    """Solo los workers de la cola `scan` hablan con el runner; la API no."""
    github: GithubSettings | None = None
    """Credencial de la GitHub App. Solo el worker; nunca la API."""

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str],
        *,
        read_secret: Callable[[Path], str] = read_secret_file,
        read_pem: Callable[[Path], str] = read_pem_file,
    ) -> "Settings":
        reader = EnvReader(environ, read_secret, read_pem)
        reader.reject_unknown(forbidden=API_ONLY_VARIABLES)
        try:
            return cls._build(reader)
        except DomainError as error:
            raise ConfigurationError(
                f"invalid configuration: {error.__class__.__name__}"
            ) from error

    @classmethod
    def celery_only(
        cls,
        environ: Mapping[str, str],
        *,
        read_secret: Callable[[Path], str] = read_secret_file,
    ) -> CelerySettings:
        """Lo único que necesita el servicio `scheduler` (ADR 0008): el broker. Sin
        credenciales de base de datos ni de GitHub."""
        reader = EnvReader(environ, read_secret)
        reader.reject_unknown()
        try:
            return celery_settings(reader)[0]
        except DomainError as error:
            raise ConfigurationError(
                f"invalid configuration: {error.__class__.__name__}"
            ) from error

    @classmethod
    def _build(cls, reader: "EnvReader") -> "Settings":
        celery, fetch_timeout, scan_timeout = celery_settings(reader)
        fetch = FetchLimits(
            max_bytes=reader.integer("NAVE_FETCH_MAX_BYTES", 512 << 20, 1, 1 << 34),
            max_files=reader.integer("NAVE_FETCH_MAX_FILES", 200_000, 1, 10_000_000),
            max_duration=fetch_timeout,
        )
        scan = ScanLimits(
            max_duration=scan_timeout,
            max_output_bytes=reader.integer(
                "NAVE_SCANNER_MAX_OUTPUT_BYTES", DEFAULT_SCANNER_MAX_OUTPUT_BYTES, 1, 1 << 30
            ),
            max_results=reader.integer("NAVE_SCANNER_MAX_RESULTS", 50_000, 1, 1_000_000),
        )
        level = reader.optional("NAVE_LOG_LEVEL") or "INFO"
        if level not in _LOG_LEVELS:
            raise ConfigurationError("NAVE_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")
        sweep = SweepThresholds(
            running_stuck_after=timedelta(
                seconds=reader.integer(
                    "NAVE_SWEEP_RUNNING_STUCK_AFTER_SECONDS",
                    int(2 * celery.scan.hard.total_seconds()),
                    300,
                    604_800,
                )
            ),
            requeue_after=timedelta(
                seconds=reader.integer("NAVE_SWEEP_REQUEUE_AFTER_SECONDS", 600, 60, 86_400)
            ),
            expire_queued_after=timedelta(
                seconds=reader.integer("NAVE_SWEEP_QUEUE_EXPIRE_AFTER_SECONDS", 3600, 120, 604_800)
            ),
        )
        if sweep.running_stuck_after <= celery.scan.hard:
            raise ConfigurationError(
                "NAVE_SWEEP_RUNNING_STUCK_AFTER_SECONDS must exceed the task hard limit"
            )
        retention = RetentionPolicy(
            webhook_deliveries=_days(reader, "NAVE_RETENTION_WEBHOOK_DELIVERIES_DAYS", 30),
            scan_logs=_days(reader, "NAVE_RETENTION_SCAN_LOGS_DAYS", 30),
            published_outbox=_days(reader, "NAVE_RETENTION_OUTBOX_DAYS", 7),
            scans=_days(reader, "NAVE_RETENTION_SCANS_DAYS", 365),
        )
        return cls(
            database_url=reader.database_url("NAVE_DATABASE_URL", "NAVE_DATABASE_PASSWORD_FILE"),
            maintenance_database_url=(
                reader.database_url(
                    "NAVE_MAINTENANCE_DATABASE_URL", "NAVE_MAINTENANCE_DATABASE_PASSWORD_FILE"
                )
                if reader.optional("NAVE_MAINTENANCE_DATABASE_URL")
                else None
            ),
            runner=_runner_settings(reader),
            github=_github_settings(reader),
            celery=celery,
            fetch_limits=fetch,
            scan_limits=scan,
            max_findings_per_scanner=scan.max_results,
            max_sarif_findings=reader.integer("NAVE_MAX_SARIF_FINDINGS", 10_000, 1, 25_000),
            fingerprint_key=FingerprintKey.from_hex(reader.secret("NAVE_FINGERPRINT_KEY_FILE")),
            log_level=level,
            retention=retention,
            sweep=sweep,
        )


def _runner_settings(reader: "EnvReader") -> RunnerClientSettings | None:
    url = reader.optional("NAVE_RUNNER_URL")
    if url is None:
        return None
    HttpTransport(url)  # valida esquema, host y ausencia de credenciales en la URL
    versions = {
        name: reader.required(name) for name in ("NAVE_SEMGREP_VERSION", "NAVE_TRIVY_VERSION")
    }
    if not all(_SCANNER_VERSION.fullmatch(v) for v in versions.values()):
        raise ConfigurationError("scanner versions have an invalid format")
    return RunnerClientSettings(
        url=url,
        key=RunnerKey.from_hex(reader.secret("NAVE_RUNNER_HMAC_KEY_FILE")),
        semgrep_version=versions["NAVE_SEMGREP_VERSION"],
        trivy_version=versions["NAVE_TRIVY_VERSION"],
    )


def _github_settings(reader: "EnvReader") -> GithubSettings | None:
    raw_id = reader.optional("NAVE_GITHUB_APP_ID")
    if raw_id is None:
        return None
    if _APP_ID.fullmatch(raw_id) is None:
        raise ConfigurationError("NAVE_GITHUB_APP_ID must be a positive integer")
    api_url = reader.optional("NAVE_GITHUB_API_URL") or DEFAULT_BASE_URL
    proxy = reader.optional("NAVE_EGRESS_PROXY_URL")
    HttpsGithubTransport(base_url=api_url, proxy_url=proxy)  # valida esquema, host y credenciales
    try:
        key = AppPrivateKey.from_pem(reader.pem("NAVE_GITHUB_APP_PRIVATE_KEY_FILE"))
    except DomainError as error:
        raise ConfigurationError("NAVE_GITHUB_APP_PRIVATE_KEY_FILE is not a usable key") from error
    return GithubSettings(
        app_id=int(raw_id),
        private_key=key,
        api_url=api_url,
        egress_proxy_url=proxy,
        sarif_upload=reader.switch("NAVE_SARIF_UPLOAD", default=False),
        allowed_accounts=_allowed_accounts(reader.optional("NAVE_GITHUB_ALLOWED_ACCOUNTS")),
    )


def _allowed_accounts(raw: str | None) -> frozenset[str]:
    if raw is None:
        return frozenset()
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    if len(names) > _MAX_ALLOWED_ACCOUNTS or not all(is_valid_account_login(n) for n in names):
        raise ConfigurationError("NAVE_GITHUB_ALLOWED_ACCOUNTS must list valid account names")
    return frozenset(names)


def _days(reader: "EnvReader", name: str, default: int) -> timedelta:
    return timedelta(days=reader.integer(name, default, 1, 3650))


def celery_settings(reader: "EnvReader") -> tuple[CelerySettings, timedelta, timedelta]:
    fetch_timeout = timedelta(seconds=reader.integer("NAVE_FETCH_TIMEOUT_SECONDS", 300, 1, 3600))
    scan_timeout = timedelta(seconds=reader.integer("NAVE_SCAN_TIMEOUT_SECONDS", 600, 1, 7200))
    default_soft = fetch_timeout + _EXPECTED_SCANNERS * scan_timeout + _TASK_MARGIN
    soft = timedelta(
        seconds=reader.integer(
            "NAVE_SCAN_TASK_SOFT_SECONDS", int(default_soft.total_seconds()), 1, 86_400
        )
    )
    if soft <= fetch_timeout + scan_timeout:
        raise ConfigurationError(
            "NAVE_SCAN_TASK_SOFT_SECONDS must exceed fetch time plus at least one scan"
        )
    hard = timedelta(
        seconds=reader.integer(
            "NAVE_SCAN_TASK_HARD_SECONDS", int((soft + _KILL_GRACE).total_seconds()), 1, 90_000
        )
    )
    celery = CelerySettings(
        broker_url=reader.redis_url(),
        scan=TaskTimeLimits(soft, hard),
        retry=RetryPolicy(max_retries=reader.integer("NAVE_TASK_MAX_RETRIES", 5, 0, 20)),
        max_queue_depth=reader.integer("NAVE_QUEUE_MAX_DEPTH", 1000, 1, 1_000_000),
        max_tasks_per_child=reader.integer("NAVE_WORKER_MAX_TASKS_PER_CHILD", 25, 1, 10_000),
    )
    return celery, fetch_timeout, scan_timeout


class EnvReader:
    def __init__(
        self,
        environ: Mapping[str, str],
        read_secret: Callable[[Path], str],
        read_pem: Callable[[Path], str] = read_pem_file,
    ) -> None:
        self._env = environ
        self._read_secret = read_secret
        self._read_pem = read_pem

    def reject_unknown(self, *, forbidden: frozenset[str] = frozenset()) -> None:
        unknown = sorted(k for k in self._env if k.startswith(_PREFIX) and k not in KNOWN_VARIABLES)
        if unknown:
            raise ConfigurationError(f"unknown settings: {', '.join(unknown)}")
        misplaced = sorted(k for k in forbidden if self._env.get(k))
        if misplaced:
            raise ConfigurationError(
                f"settings that belong to another service: {', '.join(misplaced)}"
            )

    def optional(self, name: str) -> str | None:
        return self._env.get(name) or None

    def required(self, name: str) -> str:
        value = self.optional(name)
        if value is None:
            raise ConfigurationError(f"{name} is required")
        return value

    def integer(self, name: str, default: int, low: int, high: int) -> int:
        raw = self.optional(name)
        if raw is None:
            return default
        if _INT.fullmatch(raw) is None or not low <= int(raw) <= high:
            raise ConfigurationError(f"{name} must be an integer in [{low}, {high}]")
        return int(raw)

    def secret(self, name: str) -> str:
        return self._read_secret(Path(self.required(name)))

    def pem(self, name: str) -> str:
        return self._read_pem(Path(self.required(name)))

    def switch(self, name: str, *, default: bool) -> bool:
        raw = self.optional(name)
        if raw is None:
            return default
        if raw not in {"on", "off"}:
            raise ConfigurationError(f"{name} must be 'on' or 'off'")
        return raw == "on"

    def database_url(self, url_name: str, secret_name: str) -> URL:
        try:
            url = make_url(self.required(url_name))
        except ArgumentError as error:
            raise ConfigurationError(f"{url_name} is not a valid URL") from error
        if url.password is not None:
            raise ConfigurationError(f"{url_name} must not contain a password")
        if not url.username:
            raise ConfigurationError(f"{url_name} must name the database user")
        return url.set(password=self.secret(secret_name))

    def redis_url(self) -> str:
        raw = self.required("NAVE_REDIS_URL")
        try:
            parts = urlsplit(raw)
            host, port = parts.hostname, parts.port
        except ValueError as error:
            raise ConfigurationError("NAVE_REDIS_URL is not a valid URL") from error
        if parts.scheme not in {"redis", "rediss"} or not host:
            raise ConfigurationError("NAVE_REDIS_URL must be a redis:// or rediss:// URL")
        if parts.password is not None:
            raise ConfigurationError("NAVE_REDIS_URL must not contain a password")
        if parts.query or parts.fragment:
            raise ConfigurationError("NAVE_REDIS_URL must not have a query or fragment")
        user = quote(parts.username or "default", safe="")
        password = quote(self.secret("NAVE_REDIS_PASSWORD_FILE"), safe="")
        netloc = f"{user}:{password}@{host if ':' not in host else f'[{host}]'}"
        if port is not None:
            netloc += f":{port}"
        return f"{parts.scheme}://{netloc}{parts.path or '/0'}"
