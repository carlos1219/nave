import pytest
from hypothesis import given
from hypothesis import strategies as st

from nave.domain.errors import InvalidGitRefError
from nave.domain.value_objects.git_ref import GitRef


@pytest.mark.parametrize(
    "raw",
    [
        "main",
        "refs/heads/main",
        "feature/ñandú",
        "release-1.2.3",
        "refs/pull/12/head",
        "v1.0",
        "a" * 255,
    ],
)
def test_accepts_valid_refs(raw: str) -> None:
    assert str(GitRef(raw)) == raw


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("", "vacía"),
        ("-x", "guion inicial"),
        ("--upload-pack=touch /tmp/pwned", "inyección de opción de git"),
        ("a..b", "doble punto"),
        ("a//b", "doble barra"),
        ("/a", "barra inicial"),
        ("a/", "barra final"),
        ("a.", "punto final"),
        ("a.lock", "sufijo .lock"),
        ("a/b.lock/c", "componente .lock"),
        ("a/.b", "componente oculto"),
        (".a", "punto inicial"),
        ("@", "arroba sola"),
        ("a@{1}", "secuencia @{"),
        ("a b", "espacio"),
        ("a~b", "virgulilla"),
        ("a^b", "circunflejo"),
        ("a:b", "dos puntos"),
        ("a?b", "interrogación"),
        ("a*b", "asterisco"),
        ("a[b", "corchete"),
        ("a\\b", "barra invertida"),
        ("a\x00b", "byte nulo"),
        ("a\nb", "salto de línea"),
        ("a\x1b[31mb", "escape ANSI"),
        ("a\x7fb", "DEL"),
        ("a\N{RIGHT-TO-LEFT OVERRIDE}b", "override bidi (Trojan Source)"),
        ("a\N{ZERO WIDTH SPACE}b", "espacio de ancho cero"),
        ("a\N{NO-BREAK SPACE}b", "espacio no separable"),
        ("a" * 256, "más de 255 bytes"),
        ("ñ" * 128, "más de 255 bytes en UTF-8"),
        ("a\ud800b", "sustituto suelto"),
    ],
)
def test_rejects_invalid_refs(raw: str, why: str) -> None:
    with pytest.raises(InvalidGitRefError):
        GitRef(raw)


def test_rejects_non_string() -> None:
    with pytest.raises(InvalidGitRefError):
        GitRef(42)  # type: ignore[arg-type]


@given(st.text())
def test_arbitrary_text_is_either_valid_or_rejected_cleanly(raw: str) -> None:
    try:
        ref = GitRef(raw)
    except InvalidGitRefError:
        return
    assert not ref.value.startswith("-")
    assert ".." not in ref.value
    assert len(ref.value.encode("utf-8")) <= 255


def test_rejects_huge_input_without_scanning_it() -> None:
    with pytest.raises(InvalidGitRefError, match="bytes"):
        GitRef("a" * 10_000_000)
