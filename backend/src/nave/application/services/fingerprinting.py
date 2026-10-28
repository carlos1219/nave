"""Fingerprint estable de findings (docs/architecture/puertos-y-contratos.md §5)."""

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.validation import fullmatch_str
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.rule_id import RuleId
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.source_path import SourcePath

FINGERPRINT_VERSION: Final = "v1"
_MIN_KEY_BYTES: Final = 32
_KEYED_VERSION: Final = "v1-hmac"
_KEY_HEX: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)


@dataclass(frozen=True, slots=True)
class FingerprintKey:
    """Clave del servidor para la huella de los hallazgos `SECRET` (AM-805).

    El locator de un secreto es el SHA-256 de su valor; con ruta y regla conocidas, quien
    copie la base de datos podría probar candidatos de baja entropía. Con HMAC y una clave
    que solo tiene el worker, la copia no basta. Rotar la clave cambia todas las huellas
    de secretos (se reabren como nuevos): es una decisión operativa, no un fallo.
    """

    material: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.material) < _MIN_KEY_BYTES:
            raise InvalidValueError("fingerprint_key", f"must be at least {_MIN_KEY_BYTES} bytes")

    @classmethod
    def from_hex(cls, value: str) -> "FingerprintKey":
        """Formato del secreto: 64 hex en minúsculas (`openssl rand -hex 32`)."""
        if not fullmatch_str(_KEY_HEX, value):
            raise InvalidValueError("fingerprint_key", "must be 64 lowercase hex characters")
        return cls(bytes.fromhex(value))


def _encode(part: str) -> bytes:
    # Prefijo de longitud: ninguna combinación de campos puede colisionar con otra por
    # concatenación (p. ej. rule "a|b" + path "c" frente a rule "a" + path "b|c").
    data = part.encode("utf-8", "surrogatepass")
    return str(len(data)).encode("ascii") + b":" + data


def compute_fingerprint(  # noqa: PLR0913 - campos del contrato de huella
    *,
    scanner_kind: ScannerKind,
    category: FindingCategory,
    rule_id: RuleId,
    path: SourcePath | None,
    locator: str,
    key: FingerprintKey | None = None,
) -> Fingerprint:
    """No incluye números de línea: mover código no crea findings nuevos.

    Los hallazgos `SECRET` exigen `key` (HMAC-SHA256, AM-805); el resto usa SHA-256 sin
    clave porque su locator no deriva de un valor secreto.
    """
    keyed = category is FindingCategory.SECRET
    if keyed and key is None:
        raise InvalidValueError("fingerprint_key", "is required for SECRET findings")
    parts = (
        _KEYED_VERSION if keyed else FINGERPRINT_VERSION,
        scanner_kind.value,
        category.value,
        rule_id.value,
        path.value if path is not None else "",
        locator,
    )
    message = b"".join(_encode(part) for part in parts)
    if keyed and key is not None:
        return Fingerprint(hmac.new(key.material, message, hashlib.sha256).hexdigest())
    return Fingerprint(hashlib.sha256(message).hexdigest())


def content_hash(text: str) -> str:
    """SHA-256 del texto con los espacios normalizados (reindentar no cambia la huella)."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()
