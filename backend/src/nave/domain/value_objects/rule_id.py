import re
from dataclasses import dataclass
from typing import Final

from nave.domain.errors import InvalidReferenceError, InvalidRuleIdError
from nave.domain.validation import fullmatch_str

# Cubre IDs de Semgrep (python.lang.security.audit.eval-detected), Trivy (CVE-2024-1234,
# AVD-AWS-0086, aws-access-key-id) y escáneres futuros con espacios de nombres.
_RULE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", re.ASCII)
_CWE: Final = re.compile(r"CWE-[1-9][0-9]{0,5}", re.ASCII)
# El programa CVE empezó en 1999: un año anterior delata un identificador inventado.
_CVE: Final = re.compile(r"CVE-(?:1999|2[0-9]{3})-[0-9]{4,19}", re.ASCII)


@dataclass(frozen=True, slots=True)
class RuleId:
    value: str

    def __post_init__(self) -> None:
        if not fullmatch_str(_RULE, self.value):
            raise InvalidRuleIdError("rule_id", "must be 1-256 characters from [A-Za-z0-9._:/@+-]")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CweId:
    """Identificador CWE, p. ej. `CWE-79`."""

    value: str

    def __post_init__(self) -> None:
        if not fullmatch_str(_CWE, self.value):
            raise InvalidReferenceError("cwe", "must look like CWE-<number>")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CveId:
    """Identificador CVE, p. ej. `CVE-2024-3094`."""

    value: str

    def __post_init__(self) -> None:
        if not fullmatch_str(_CVE, self.value):
            raise InvalidReferenceError("cve", "must look like CVE-<year>-<number>")

    def __str__(self) -> str:
        return self.value
