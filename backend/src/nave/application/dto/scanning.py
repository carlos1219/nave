import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str, is_int_in_range
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity

_IMAGE_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
_SHA256: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)
_VERSION: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", re.ASCII)
_MAX_RESULTS: Final = 1_000_000


@dataclass(frozen=True, slots=True)
class ScanLimits:
    max_duration: timedelta
    max_output_bytes: int
    max_results: int

    def __post_init__(self) -> None:
        if not timedelta(0) < self.max_duration <= timedelta(hours=2):
            raise InvalidValueError("max_duration", "must be in (0, 2h]")
        if not is_int_in_range(self.max_output_bytes, 1, 1 << 30):
            raise InvalidValueError("max_output_bytes", "must be in [1, 1 GiB]")
        if not is_int_in_range(self.max_results, 1, _MAX_RESULTS):
            raise InvalidValueError("max_results", f"must be in [1, {_MAX_RESULTS}]")


@dataclass(frozen=True, slots=True)
class FetchLimits:
    max_bytes: int
    max_files: int
    max_duration: timedelta

    def __post_init__(self) -> None:
        if not is_int_in_range(self.max_bytes, 1, 1 << 34):
            raise InvalidValueError("max_bytes", "must be in [1, 16 GiB]")
        if not is_int_in_range(self.max_files, 1, 10_000_000):
            raise InvalidValueError("max_files", "must be in [1, 10M]")
        if not timedelta(0) < self.max_duration <= timedelta(hours=1):
            raise InvalidValueError("max_duration", "must be in (0, 1h]")


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedResult:
    """Un resultado tal como lo entrega el adaptador de un escáner.

    Equivale a un `result` de SARIF 2.1.0 con las propiedades que NAVE necesita ya
    resueltas por el adaptador: severidad normalizada (tabla de cada adaptador),
    categoría y `locator`. Los textos y la ruta siguen siendo **no confiables**:
    los valida y sanea `application.services.normalizer`.

    `locator` según categoría: DEPENDENCY `ecosistema:paquete`; SECRET el SHA-256 en
    hexadecimal del valor (calculado dentro del sandbox; el valor nunca sale de él);
    IAC el identificador del recurso; SAST vacío (se deriva del snippet).
    """

    rule_id: str
    category: FindingCategory
    severity: Severity
    title: str
    message: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    snippet: str | None = None
    locator: str = ""
    cwe: tuple[str, ...] = ()
    cve: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ScannerOutput:
    kind: ScannerKind
    scanner_version: str
    image_digest: str
    output_sha256: str
    results: tuple[NormalizedResult, ...]
    truncated: bool

    def __post_init__(self) -> None:
        if not fullmatch_str(_VERSION, self.scanner_version):
            raise InvalidValueError("scanner_version", "is not a valid version string")
        if not fullmatch_str(_IMAGE_DIGEST, self.image_digest):
            raise InvalidValueError("image_digest", "must be sha256:<64 hex>")
        if not fullmatch_str(_SHA256, self.output_sha256):
            raise InvalidValueError("output_sha256", "must be 64 hex characters")
        if not isinstance(self.results, tuple):
            raise InvalidValueError("results", "must be a tuple")
        if len(self.results) > _MAX_RESULTS:
            raise InvalidValueError("results", f"must not exceed {_MAX_RESULTS} entries")
