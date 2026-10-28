import pytest

from nave.delivery.http.strict_json import MAX_DEPTH, StrictJsonError, loads_strict


def test_parses_ordinary_documents() -> None:
    assert loads_strict(b'{"a":[1,2,{"b":null}],"c":"\xc3\xa9"}') == {
        "a": [1, 2, {"b": None}],
        "c": "é",
    }


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"x":{"a":1,"a":1}}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b"\xff\xfe",
        b"",
        b"{",
        b'{"a":1} trailing',
        b"[" * (MAX_DEPTH + 2) + b"]" * (MAX_DEPTH + 2),
    ],
)
def test_rejects_ambiguous_or_hostile_documents(raw: bytes) -> None:
    with pytest.raises(StrictJsonError):
        loads_strict(raw)


def test_very_deep_nesting_does_not_crash() -> None:
    with pytest.raises(StrictJsonError):
        loads_strict(b"[" * 100_000 + b"]" * 100_000)


def test_error_does_not_echo_the_input() -> None:
    with pytest.raises(StrictJsonError) as raised:
        loads_strict(b'{"secret-token-123":1,"secret-token-123":2}')
    assert "secret-token" not in str(raised.value)
