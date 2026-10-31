"""AM-805: la huella de un secreto no se puede verificar sin la clave del servidor."""

import hashlib
import re

import pytest

from nave.application.dto.scanning import NormalizedResult
from nave.application.services.fingerprinting import FingerprintKey, compute_fingerprint
from nave.application.services.normalizer import normalize_output
from nave.domain.errors import InvalidValueError
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.rule_id import RuleId
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.source_path import SourcePath
from tests.unit.application.builders import output

KEY_A = FingerprintKey(bytes([1]) * 32)
KEY_B = FingerprintKey(bytes([2]) * 32)
CANDIDATE = "hunter2-password"
LOCATOR = hashlib.sha256(CANDIDATE.encode()).hexdigest()


def secret_result(locator: str = LOCATOR) -> NormalizedResult:
    return NormalizedResult(
        rule_id="generic-password",
        category=FindingCategory.SECRET,
        severity=Severity.HIGH,
        title="Password",
        message="Password detected",
        path="config/.env",
        start_line=1,
        end_line=1,
        locator=locator,
    )


def fingerprint(
    key: FingerprintKey | None, category: FindingCategory = FindingCategory.SECRET
) -> Fingerprint:
    return compute_fingerprint(
        scanner_kind=ScannerKind("trivy"),
        category=category,
        rule_id=RuleId("generic-password"),
        path=SourcePath("config/.env"),
        locator=LOCATOR,
        key=key,
    )


def unkeyed_digest() -> str:
    # Lo que calcularía quien tiene una copia de la BD y adivina el secreto.
    parts = ("v1", "trivy", "SECRET", "generic-password", "config/.env", LOCATOR)
    digest = hashlib.sha256()
    for part in parts:
        data = part.encode()
        digest.update(str(len(data)).encode() + b":" + data)
    return digest.hexdigest()


def test_attacker_without_key_cannot_reproduce_fingerprint() -> None:
    assert fingerprint(KEY_A).value != unkeyed_digest()


def test_fingerprint_depends_on_the_key() -> None:
    assert fingerprint(KEY_A) != fingerprint(KEY_B)


def test_fingerprint_is_stable_for_the_same_key() -> None:
    assert fingerprint(KEY_A) == fingerprint(KEY_A)


def test_secret_without_key_is_refused() -> None:
    with pytest.raises(InvalidValueError):
        fingerprint(None)


def test_non_secret_categories_do_not_need_a_key() -> None:
    assert fingerprint(None, FindingCategory.DEPENDENCY) == fingerprint(
        KEY_A, FindingCategory.DEPENDENCY
    )


def test_normalizer_applies_the_key_to_secrets() -> None:
    a = normalize_output(output(secret_result()), max_findings=5, secret_key=KEY_A)
    b = normalize_output(output(secret_result()), max_findings=5, secret_key=KEY_B)
    assert a.findings[0].fingerprint != b.findings[0].fingerprint
    assert a.findings[0].fingerprint.value != unkeyed_digest()


@pytest.mark.parametrize("material", [b"", b"short", bytes(31)])
def test_short_keys_are_rejected(material: bytes) -> None:
    with pytest.raises(InvalidValueError):
        FingerprintKey(material)


@pytest.mark.parametrize(
    "value", ["", "ab", "A" * 64, "g" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"]
)
def test_key_format_is_strict(value: str) -> None:
    with pytest.raises(InvalidValueError):
        FingerprintKey.from_hex(value)


def test_key_is_never_printed() -> None:
    key = FingerprintKey.from_hex("ab" * 32)
    assert not re.search("ab" * 8, repr(key))
