"""Configuración del runner desde el entorno y Docker secrets.

Mismo criterio que el worker: solo variables `NAVE_RUNNER_*`, las desconocidas se
rechazan, la clave solo se acepta como ruta a fichero y los errores nombran la variable,
nunca el valor. Las imágenes se aceptan únicamente fijadas por digest (AM-402).
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from nave_runner.protocol import parse_key

PREFIX: Final = "NAVE_RUNNER_"
_IMAGE: Final = re.compile(r"[a-z0-9][a-z0-9._/:-]{0,200}@(sha256:[0-9a-f]{64})", re.ASCII)
_VERSION: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", re.ASCII)
_KIND: Final = re.compile(r"[a-z][a-z0-9-]{0,30}[a-z0-9]", re.ASCII)
_RESOURCE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", re.ASCII)
_PROXY: Final = re.compile(r"http://[a-z0-9][a-z0-9.-]{0,100}:[0-9]{1,5}", re.ASCII)
_PATH: Final = re.compile(r"/[A-Za-z0-9_./-]{1,200}", re.ASCII)
_INT: Final = re.compile(r"[0-9]{1,12}", re.ASCII)
_MAX_KEY_FILE: Final = 1024
_GIB: Final = 1 << 30
DEFAULT_MAX_OUTPUT_BYTES: Final = 64 << 20

KNOWN: Final = frozenset(
    {
        "HMAC_KEY_FILE",
        "LISTEN_HOST",
        "LISTEN_PORT",
        "ENGINE_BINARY",
        "RUNTIME",
        "SECCOMP_PROFILE",
        "SCANNERS",
        "FETCH_IMAGE",
        "EGRESS_NETWORK",
        "EGRESS_PROXY_URL",
        "TRIVY_DB_VOLUME",
        "WORKSPACE_BYTES",
        "MAX_FETCH_BYTES",
        "MAX_FETCH_FILES",
        "MAX_FETCH_SECONDS",
        "MAX_SCAN_SECONDS",
        "MAX_OUTPUT_BYTES",
        "MAX_CONCURRENT",
        "ORPHAN_MAX_AGE_SECONDS",
        "SWEEP_INTERVAL_SECONDS",
    }
)


class ConfigurationError(Exception):
    """Configuración inválida. Impide arrancar."""


@dataclass(frozen=True, slots=True)
class ScannerImage:
    kind: str
    reference: str
    digest: str
    version: str


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    key: bytes = field(repr=False)
    listen_host: str
    listen_port: int
    engine_binary: str
    runtime: str | None
    seccomp_profile: str | None
    fetch_image: str
    scanners: Mapping[str, ScannerImage]
    egress_network: str
    egress_proxy_url: str
    trivy_db_volume: str
    workspace_bytes: int
    max_fetch_bytes: int
    max_fetch_files: int
    max_fetch_seconds: int
    max_scan_seconds: int
    max_output_bytes: int
    max_concurrent: int
    orphan_max_age_seconds: int
    sweep_interval_seconds: int


def read_secret_file(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_KEY_FILE + 1)
    except OSError as error:
        raise ConfigurationError(f"cannot read secret file ({type(error).__name__})") from error
    if len(raw) > _MAX_KEY_FILE:
        raise ConfigurationError("secret file is too large")
    return raw.decode("ascii", errors="replace").strip()


def load_config(
    environ: Mapping[str, str],
    read_secret: Callable[[Path], str] = read_secret_file,
) -> RunnerConfig:
    allowed = KNOWN | _per_scanner_names(environ.get(PREFIX + "SCANNERS", ""))
    for name in environ:
        if name.startswith(PREFIX) and name[len(PREFIX) :] not in allowed:
            raise ConfigurationError(f"unknown variable {name}")
    env = _Env(environ)
    try:
        key = parse_key(read_secret(Path(env.required("HMAC_KEY_FILE"))))
    except ValueError as error:
        raise ConfigurationError("NAVE_RUNNER_HMAC_KEY_FILE does not hold a valid key") from error
    workspace_bytes = env.integer("WORKSPACE_BYTES", _GIB, 1 << 20, 64 * _GIB)
    max_fetch_bytes = env.integer("MAX_FETCH_BYTES", _GIB, 1, 64 * _GIB)
    if max_fetch_bytes > workspace_bytes:
        raise ConfigurationError("NAVE_RUNNER_MAX_FETCH_BYTES must not exceed the workspace size")
    max_fetch_seconds = env.integer("MAX_FETCH_SECONDS", 3600, 1, 3600)
    max_scan_seconds = env.integer("MAX_SCAN_SECONDS", 7200, 1, 7200)
    scanners = _scanners(env)
    return RunnerConfig(
        key=key,
        listen_host=env.optional("LISTEN_HOST") or "0.0.0.0",  # noqa: S104 - red `runner` interna
        listen_port=env.integer("LISTEN_PORT", 8080, 1, 65535),
        engine_binary=env.matching("ENGINE_BINARY", _PATH, "/usr/local/bin/docker")
        or "/usr/local/bin/docker",
        runtime=env.matching("RUNTIME", _RESOURCE, None),
        seccomp_profile=env.matching("SECCOMP_PROFILE", _PATH, None),
        fetch_image=env.image("FETCH_IMAGE")[0],
        scanners=scanners,
        egress_network=env.matching("EGRESS_NETWORK", _RESOURCE, "nave-egress") or "nave-egress",
        egress_proxy_url=env.required_matching("EGRESS_PROXY_URL", _PROXY),
        trivy_db_volume=env.matching("TRIVY_DB_VOLUME", _RESOURCE, "nave-trivy-db")
        or "nave-trivy-db",
        workspace_bytes=workspace_bytes,
        max_fetch_bytes=max_fetch_bytes,
        max_fetch_files=env.integer("MAX_FETCH_FILES", 200_000, 1, 10_000_000),
        max_fetch_seconds=max_fetch_seconds,
        max_scan_seconds=max_scan_seconds,
        max_output_bytes=env.integer("MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES, 1, 1 << 30),
        max_concurrent=env.integer("MAX_CONCURRENT", 4, 1, 64),
        orphan_max_age_seconds=env.integer(
            "ORPHAN_MAX_AGE_SECONDS",
            2 * (max_fetch_seconds + max_scan_seconds),
            max_fetch_seconds + max_scan_seconds,
            7 * 86400,
        ),
        sweep_interval_seconds=env.integer("SWEEP_INTERVAL_SECONDS", 300, 10, 86400),
    )


def _per_scanner_names(raw: str) -> frozenset[str]:
    names: set[str] = set()
    for kind in raw.split(","):
        suffix = kind.strip().upper().replace("-", "_")
        names |= {f"IMAGE_{suffix}", f"VERSION_{suffix}"}
    return frozenset(names)


def _scanners(env: "_Env") -> Mapping[str, ScannerImage]:
    kinds = [k.strip() for k in env.required("SCANNERS").split(",")]
    if not kinds or len(set(kinds)) != len(kinds) or not all(_KIND.fullmatch(k) for k in kinds):
        raise ConfigurationError("NAVE_RUNNER_SCANNERS must list unique scanner kinds")
    result: dict[str, ScannerImage] = {}
    for kind in kinds:
        suffix = kind.upper().replace("-", "_")
        reference, digest = env.image(f"IMAGE_{suffix}")
        version = env.required_matching(f"VERSION_{suffix}", _VERSION)
        result[kind] = ScannerImage(kind, reference, digest, version)
    return result


class _Env:
    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def optional(self, name: str) -> str | None:
        return self._environ.get(PREFIX + name) or None

    def required(self, name: str) -> str:
        value = self.optional(name)
        if value is None:
            raise ConfigurationError(f"{PREFIX}{name} is required")
        return value

    def matching(self, name: str, pattern: re.Pattern[str], default: str | None) -> str | None:
        value = self.optional(name)
        if value is None:
            return default
        if not pattern.fullmatch(value):
            raise ConfigurationError(f"{PREFIX}{name} has an invalid format")
        return value

    def required_matching(self, name: str, pattern: re.Pattern[str]) -> str:
        value = self.required(name)
        if not pattern.fullmatch(value):
            raise ConfigurationError(f"{PREFIX}{name} has an invalid format")
        return value

    def integer(self, name: str, default: int, low: int, high: int) -> int:
        raw = self.optional(name)
        if raw is None:
            return default
        if not _INT.fullmatch(raw) or not low <= int(raw) <= high:
            raise ConfigurationError(f"{PREFIX}{name} must be an integer in [{low}, {high}]")
        return int(raw)

    def image(self, name: str) -> tuple[str, str]:
        """(referencia completa, digest). Una etiqueta móvil se rechaza (AM-402)."""
        value = self.required(name)
        match = _IMAGE.fullmatch(value)
        if match is None:
            raise ConfigurationError(f"{PREFIX}{name} must be an image pinned by sha256 digest")
        return value, match.group(1)
