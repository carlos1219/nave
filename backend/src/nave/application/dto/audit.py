import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.timestamps import ensure_utc
from nave.domain.validation import fullmatch_str, is_str_with_length
from nave.domain.value_objects.identifiers import OrganizationId, UserId
from nave.domain.value_objects.text import is_safe_text

_ACTION: Final = re.compile(r"[a-z]+(?:[._][a-z]+){1,3}", re.ASCII)
_TARGET_TYPE: Final = re.compile(r"[a-z_]{1,32}", re.ASCII)
_MAX_DETAIL: Final = 512
_MAX_DETAILS: Final = 16
_MAX_TARGET_ID: Final = 128


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditEntry:
    """Entrada del audit log append-only (CLAUDE.md §12).

    `details` admite solo texto seguro y acotado. Nunca contiene secretos ni texto
    de findings: solo identificadores y valores de enums.
    """

    occurred_at: datetime
    action: str
    actor: UserId | None
    organization_id: OrganizationId | None
    target_type: str
    target_id: str
    details: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        ensure_utc(self.occurred_at, "occurred_at")
        if not fullmatch_str(_ACTION, self.action):
            raise InvalidValueError("action", "must look like 'finding.triaged'")
        if not fullmatch_str(_TARGET_TYPE, self.target_type):
            raise InvalidValueError("target_type", "must be a lowercase identifier")
        if not is_str_with_length(self.target_id, 1, _MAX_TARGET_ID):
            raise InvalidValueError("target_id", f"length must be in [1, {_MAX_TARGET_ID}]")
        if len(self.details) > _MAX_DETAILS:
            raise InvalidValueError("details", f"at most {_MAX_DETAILS} entries")
        for key, value in self.details.items():
            for text in (key, value):
                if not is_str_with_length(text, 0, _MAX_DETAIL) or not is_safe_text(text):
                    raise InvalidValueError("details", "entries must be short safe strings")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
