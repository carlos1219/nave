from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidValueError
from nave.domain.validation import is_int_in_range, is_str_with_length
from nave.domain.value_objects.repo_ref import RepoRef

MAX_COMMENT_LENGTH: Final = 60_000
PR_COMMENT_MARKER: Final = "<!-- nave:pr-summary -->"


@dataclass(frozen=True, slots=True)
class VcsTarget:
    installation_id: int
    repo: RepoRef

    def __post_init__(self) -> None:
        if not is_int_in_range(self.installation_id, 1, 2**63 - 1):
            raise InvalidValueError("installation_id", "must be a positive integer")


@dataclass(frozen=True, slots=True)
class PrCommentBody:
    """Markdown ya escapado. Solo lo construye `services.pr_comment`, nunca un adaptador."""

    markdown: str

    def __post_init__(self) -> None:
        if not is_str_with_length(self.markdown, 1, MAX_COMMENT_LENGTH):
            raise InvalidValueError("markdown", f"length must be in [1, {MAX_COMMENT_LENGTH}]")
        if not self.markdown.startswith(PR_COMMENT_MARKER):
            raise InvalidValueError("markdown", "must start with the NAVE marker")
