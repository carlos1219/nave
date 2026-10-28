from collections.abc import Collection
from dataclasses import dataclass

from nave.domain.value_objects.fingerprint import Fingerprint


@dataclass(frozen=True, slots=True)
class BaselineDiff:
    new: frozenset[Fingerprint]
    persisting: frozenset[Fingerprint]
    missing: frozenset[Fingerprint]


def diff_against_baseline(
    current: Collection[Fingerprint], baseline: frozenset[Fingerprint]
) -> BaselineDiff:
    """`new`: en el escaneo y no en el baseline (lo que introduce un PR)."""
    present = frozenset(current)
    return BaselineDiff(
        new=present - baseline, persisting=present & baseline, missing=baseline - present
    )
