import pytest

from nave.domain.errors import (
    InvalidFingerprintError,
    InvalidScannerKindError,
    InvalidTriggerKeyError,
)
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.trigger import TriggerKey


@pytest.mark.parametrize("raw", ["semgrep", "trivy", "go-sec", "a1", "x" * 32])
def test_scanner_kind_accepts_slugs(raw: str) -> None:
    assert str(ScannerKind(raw)) == raw


@pytest.mark.parametrize(
    "raw", ["", "s", "Semgrep", "-trivy", "trivy-", "trivy_x", "x" * 33, "../x", "a b", 1]
)
def test_scanner_kind_rejects_invalid(raw: str) -> None:
    with pytest.raises(InvalidScannerKindError):
        ScannerKind(raw)


def test_fingerprint_accepts_sha256_hex() -> None:
    raw = "ab" * 32
    assert str(Fingerprint(raw)) == raw


@pytest.mark.parametrize("raw", ["", "ab" * 31, "AB" * 32, "ab" * 32 + "\n", "zz" * 32, None])
def test_fingerprint_rejects_invalid(raw: str) -> None:
    with pytest.raises(InvalidFingerprintError):
        Fingerprint(raw)


@pytest.mark.parametrize(
    "raw", ["gh-delivery:72d3162e-cc78-11e3-81ab-4c9367dc0958", "manual:1", "a", "a" * 128]
)
def test_trigger_key_accepts_valid(raw: str) -> None:
    assert str(TriggerKey(raw)) == raw


@pytest.mark.parametrize("raw", ["", ":x", "a" * 129, "a b", "a\nb", "a/b", "ñ", 7])
def test_trigger_key_rejects_invalid(raw: str) -> None:
    with pytest.raises(InvalidTriggerKeyError):
        TriggerKey(raw)
