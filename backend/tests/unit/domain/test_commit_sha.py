import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nave.domain.errors import InvalidCommitShaError
from nave.domain.value_objects.commit_sha import CommitSha

VALID = "0123456789abcdef0123456789abcdef01234567"


def test_accepts_lowercase_sha1() -> None:
    assert str(CommitSha(VALID)) == VALID


@pytest.mark.parametrize(
    "raw",
    [
        VALID.upper(),
        VALID[:-1],
        VALID + "0",
        "g" * 40,
        VALID + "\n",
        "\n" + VALID[1:],
        "-" + VALID[1:],
        "\N{ARABIC-INDIC DIGIT ZERO}" * 40,  # dígitos arábigo-índicos: \d los aceptaría
        "\N{FULLWIDTH DIGIT ZERO}" * 40,  # dígitos de ancho completo
        "",
    ],
)
def test_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(InvalidCommitShaError):
        CommitSha(raw)


def test_rejects_non_string() -> None:
    with pytest.raises(InvalidCommitShaError):
        CommitSha(None)  # type: ignore[arg-type]


@given(st.text())
def test_arbitrary_text_is_either_valid_or_rejected_cleanly(raw: str) -> None:
    try:
        sha = CommitSha(raw)
    except InvalidCommitShaError:
        return
    assert re.fullmatch(r"[0-9a-f]{40}", sha.value, re.ASCII)
