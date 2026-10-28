"""Configuración del servicio `api` (ADR 0015).

No lee ninguna credencial del worker (clave de huella, clave de la App, runner,
mantenimiento): si alguna aparece en su entorno, el arranque falla. Así un contenedor
expuesto a Internet no puede heredar secretos por un error de despliegue.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from sqlalchemy import URL

from nave.application.dto.auth import SessionPolicy
from nave.bootstrap.config import (
    WORKER_ONLY_VARIABLES,
    ConfigurationError,
    EnvReader,
    celery_settings,
    read_secret_file,
)
from nave.domain.errors import DomainError
from nave.infrastructure.auth.oidc import OidcClientConfig, origin_of
from nave.infrastructure.queue.settings import CelerySettings

MIN_WEBHOOK_SECRET_CHARS: Final = 32
MAX_PROXY_HOPS: Final = 3
_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


@dataclass(frozen=True, slots=True)
class ApiSettings:
    database_url: URL = field(repr=False)
    celery: CelerySettings
    webhook_secrets: tuple[bytes, ...] = field(repr=False)
    """Secreto actual y, durante una rotación, el anterior. Nunca más de dos."""
    scan_fork_pull_requests: bool
    log_level: str
    public_origin: str
    """Origen público de la aplicación (esquema://host[:puerto]); fija `Origin` y `redirect_uri`."""
    oidc: OidcClientConfig
    session: SessionPolicy
    trusted_proxy_hops: int
    """Proxies inversos de confianza delante de la API; fija de dónde sale la IP del cliente."""

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str],
        *,
        read_secret: Callable[[Path], str] = read_secret_file,
    ) -> "ApiSettings":
        reader = EnvReader(environ, read_secret)
        reader.reject_unknown(forbidden=WORKER_ONLY_VARIABLES)
        try:
            return cls._build(reader)
        except DomainError as error:
            raise ConfigurationError(
                f"invalid configuration: {error.__class__.__name__}"
            ) from error

    @classmethod
    def _build(cls, reader: EnvReader) -> "ApiSettings":
        celery, _fetch, _scan = celery_settings(reader)
        level = reader.optional("NAVE_LOG_LEVEL") or "INFO"
        if level not in _LOG_LEVELS:
            raise ConfigurationError("NAVE_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")
        secrets = [reader.secret("NAVE_GITHUB_WEBHOOK_SECRET_FILE")]
        if reader.optional("NAVE_GITHUB_WEBHOOK_SECRET_PREVIOUS_FILE"):
            secrets.append(reader.secret("NAVE_GITHUB_WEBHOOK_SECRET_PREVIOUS_FILE"))
        if any(len(s) < MIN_WEBHOOK_SECRET_CHARS for s in secrets):
            raise ConfigurationError(
                f"webhook secrets must have at least {MIN_WEBHOOK_SECRET_CHARS} characters"
            )
        if len(set(secrets)) != len(secrets):
            raise ConfigurationError("the previous webhook secret must differ from the current one")
        insecure = reader.switch("NAVE_DEV_INSECURE_HTTP", default=False)
        public_origin = _public_origin(reader.required("NAVE_PUBLIC_URL"), insecure=insecure)
        oidc = _oidc_config(reader, public_origin, insecure=insecure)
        session = SessionPolicy(
            absolute=timedelta(hours=reader.integer("NAVE_SESSION_ABSOLUTE_HOURS", 12, 1, 72)),
            idle=timedelta(minutes=reader.integer("NAVE_SESSION_IDLE_MINUTES", 30, 5, 720)),
        )
        return cls(
            database_url=reader.database_url("NAVE_DATABASE_URL", "NAVE_DATABASE_PASSWORD_FILE"),
            celery=celery,
            webhook_secrets=tuple(s.encode("utf-8") for s in secrets),
            scan_fork_pull_requests=reader.switch("NAVE_SCAN_FORK_PULL_REQUESTS", default=False),
            log_level=level,
            public_origin=public_origin,
            oidc=oidc,
            session=session,
            trusted_proxy_hops=reader.integer("NAVE_TRUSTED_PROXY_HOPS", 0, 0, MAX_PROXY_HOPS),
        )


def _public_origin(raw: str, *, insecure: bool) -> str:
    try:
        scheme, host, port = origin_of(raw, allow_insecure_loopback=insecure)
        parts = urlsplit(raw)
    except ValueError as error:
        raise ConfigurationError("NAVE_PUBLIC_URL must be an https:// origin") from error
    if parts.path not in {"", "/"} or parts.query:
        raise ConfigurationError("NAVE_PUBLIC_URL must be an origin without path or query")
    default = 443 if scheme == "https" else 80
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}" + (f":{port}" if port != default else "")


def _oidc_config(reader: EnvReader, public_origin: str, *, insecure: bool) -> OidcClientConfig:
    client_id = reader.required("NAVE_OIDC_CLIENT_ID")
    if len(client_id) > 255 or not client_id.isprintable():  # noqa: PLR2004
        raise ConfigurationError("NAVE_OIDC_CLIENT_ID is not valid")
    proxy = reader.optional("NAVE_OIDC_PROXY_URL")
    try:
        return OidcClientConfig(
            issuer=reader.required("NAVE_OIDC_ISSUER"),
            client_id=client_id,
            client_secret=reader.secret("NAVE_OIDC_CLIENT_SECRET_FILE"),
            redirect_uri=f"{public_origin}/api/v1/auth/callback",
            allow_insecure_loopback=insecure,
            proxy_url=proxy,
        )
    except ValueError as error:
        raise ConfigurationError(
            "NAVE_OIDC_ISSUER must be an https:// URL and "
            "NAVE_OIDC_PROXY_URL an http://host:port URL"
        ) from error
