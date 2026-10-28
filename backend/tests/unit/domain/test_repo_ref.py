import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nave.domain.errors import InvalidRepoRefError
from nave.domain.value_objects.repo_ref import RepoRef


@pytest.mark.parametrize("owner", ["a", "octo-cat", "A1", "a" * 39, "a-b-c"])
def test_accepts_valid_owners(owner: str) -> None:
    assert RepoRef(owner=owner, name="repo").owner == owner


@pytest.mark.parametrize(
    "owner",
    ["", "-a", "a-", "a--b", "a" * 40, "a_b", "a.b", "a/b", "ñandú", "a\n", "a b"],
)
def test_rejects_invalid_owners(owner: str) -> None:
    with pytest.raises(InvalidRepoRefError):
        RepoRef(owner=owner, name="repo")


@pytest.mark.parametrize("name", [".github", "a.b_c-d", "x" * 100, "-dash", "Repo1"])
def test_accepts_valid_names(name: str) -> None:
    assert RepoRef(owner="octo", name=name).name == name


BIDI_OVERRIDE = "\N{RIGHT-TO-LEFT OVERRIDE}"


@pytest.mark.parametrize(
    "name",
    [".", "..", "", "a/b", "x" * 101, "a b", "a\x00", "a\n", BIDI_OVERRIDE + "repo", "a:b"],
)
def test_rejects_invalid_names(name: str) -> None:
    with pytest.raises(InvalidRepoRefError):
        RepoRef(owner="octo", name=name)


def test_parse_and_full_name_round_trip() -> None:
    ref = RepoRef.parse("octo-org/my.repo")
    assert (ref.owner, ref.name) == ("octo-org", "my.repo")
    assert ref.full_name == str(ref) == "octo-org/my.repo"


@pytest.mark.parametrize("raw", ["octo", "octo/repo/extra", "/repo", "octo/", "../..", ""])
def test_parse_rejects_malformed_full_names(raw: str) -> None:
    with pytest.raises(InvalidRepoRefError):
        RepoRef.parse(raw)


def test_parse_rejects_non_string() -> None:
    with pytest.raises(InvalidRepoRefError):
        RepoRef.parse(b"octo/repo")  # type: ignore[arg-type]


@given(st.text(), st.text())
def test_arbitrary_text_is_either_valid_or_rejected_cleanly(owner: str, name: str) -> None:
    try:
        ref = RepoRef(owner=owner, name=name)
    except InvalidRepoRefError:
        return
    assert ref.owner.isascii()
    assert re.fullmatch(r"[A-Za-z0-9._-]+", ref.name)
    assert ref.name not in {".", ".."}
