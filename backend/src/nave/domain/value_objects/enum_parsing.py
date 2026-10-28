from enum import Enum

from nave.domain.errors import UnknownEnumValueError


def parse_enum[E: Enum](enum_type: type[E], raw: object, field: str) -> E:
    """Conversión estricta texto → enum: coincidencia exacta del valor, sin normalizar."""
    if isinstance(raw, str):
        for member in enum_type:
            if member.value == raw:
                return member
    raise UnknownEnumValueError(field, f"must be one of {[m.value for m in enum_type]}")
