import re
from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidCommitShaError
from nave.domain.validation import fullmatch_str

_PATTERN: Final = re.compile(r"[0-9a-f]{40}", re.ASCII)


@dataclass(frozen=True, slots=True)
class CommitSha:
    """SHA-1 de un commit, 40 caracteres hexadecimales en minúscula.

    GitHub no admite repositorios con SHA-256; si llegara a hacerlo, este tipo cambia por ADR.
    """

    value: str

    def __post_init__(self) -> None:
        # fullmatch y no match + "$": "$" admite un salto de línea final, que llegaría
        # a git como parte de un argumento.
        if not fullmatch_str(_PATTERN, self.value):
            raise InvalidCommitShaError("commit_sha", "must be 40 lowercase hexadecimal characters")

    def __str__(self) -> str:
        return self.value
