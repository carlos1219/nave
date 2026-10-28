from dataclasses import dataclass
from enum import Enum
from typing import Final, Self

from nave.domain.errors import InvalidStatusReasonError
from nave.domain.value_objects.enum_parsing import parse_enum
from nave.domain.value_objects.text import is_safe_text, sanitize_untrusted

MAX_DETAIL_LENGTH: Final = 256


class ReasonCode(Enum):
    FETCH_FAILED = "FETCH_FAILED"
    REPOSITORY_TOO_LARGE = "REPOSITORY_TOO_LARGE"
    SCANNER_FAILED = "SCANNER_FAILED"
    SCANNER_OUTPUT_INVALID = "SCANNER_OUTPUT_INVALID"
    PROCESSING_FAILED = "PROCESSING_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    TIMEOUT = "TIMEOUT"
    QUEUE_EXPIRED = "QUEUE_EXPIRED"
    USER_CANCELLED = "USER_CANCELLED"
    SUPERSEDED = "SUPERSEDED"

    @classmethod
    def parse(cls, raw: str) -> Self:
        return parse_enum(cls, raw, "reason_code")


@dataclass(frozen=True, slots=True)
class StatusReason:
    """Motivo de un estado terminal no exitoso: código estable + detalle acotado.

    `detail` se muestra en la UI y en logs, por eso solo admite texto seguro.
    Para texto de origen no confiable (stderr de un escáner, mensajes de excepción)
    se usa `from_untrusted`, que sanea en lugar de rechazar: un fallo al registrar
    otro fallo dejaría el escaneo sin causa.
    """

    code: ReasonCode
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.code, ReasonCode):
            raise InvalidStatusReasonError("code", "must be a ReasonCode")
        if not isinstance(self.detail, str):
            raise InvalidStatusReasonError("detail", "must be a string")
        if len(self.detail) > MAX_DETAIL_LENGTH:
            raise InvalidStatusReasonError(
                "detail", f"must not exceed {MAX_DETAIL_LENGTH} characters"
            )
        if not is_safe_text(self.detail):
            raise InvalidStatusReasonError("detail", "contains control or format characters")

    @classmethod
    def from_untrusted(cls, code: ReasonCode, text: str) -> Self:
        return cls(code=code, detail=sanitize_untrusted(text, MAX_DETAIL_LENGTH))
