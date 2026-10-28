from uuid import UUID

import pytest

from nave.domain.errors import InvalidIdentifierError
from nave.domain.value_objects.identifiers import ScanId, UserId

CANONICAL = "72d3162e-cc78-41e3-81ab-4c9367dc0958"


def test_parse_accepts_canonical_form_and_round_trips() -> None:
    scan_id = ScanId.parse(CANONICAL)
    assert scan_id.value == UUID(CANONICAL)
    assert str(scan_id) == CANONICAL


@pytest.mark.parametrize(
    "raw",
    [
        CANONICAL.upper(),
        "{" + CANONICAL + "}",
        "urn:uuid:" + CANONICAL,
        CANONICAL.replace("-", ""),
        CANONICAL + "\n",
        " " + CANONICAL,
        "",
        "not-a-uuid",
    ],
)
def test_parse_rejects_non_canonical_representations(raw: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        ScanId.parse(raw)


def test_parse_rejects_non_string() -> None:
    with pytest.raises(InvalidIdentifierError):
        ScanId.parse(123)  # type: ignore[arg-type]


def test_constructor_rejects_non_uuid() -> None:
    with pytest.raises(InvalidIdentifierError):
        ScanId(CANONICAL)  # type: ignore[arg-type]


def test_identifiers_of_different_types_are_never_equal() -> None:
    raw = UUID(CANONICAL)
    assert ScanId(raw) != UserId(raw)  # type: ignore[comparison-overlap]
    assert ScanId(raw) == ScanId(raw)
