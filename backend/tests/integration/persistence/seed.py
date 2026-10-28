"""Datos base para tests de integración: tenancy y usuarios por SQL directo (aún no hay
caso de uso que los cree) y entidades del dominio construidas con sus constructores."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine, insert

from nave.domain.entities.finding import Finding
from nave.domain.entities.scan import Scan
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.finding_text import FindingTitle
from nave.domain.value_objects.fingerprint import Fingerprint
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import (
    FindingId,
    OrganizationId,
    RepositoryId,
    ScanId,
    UserId,
)
from nave.domain.value_objects.rule_id import CveId, CweId, RuleId
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.trigger import ScanTrigger, TriggerKey
from nave.infrastructure.persistence import schema as t

T0 = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
ORG = OrganizationId(UUID(int=0x0A))
REPO = RepositoryId(UUID(int=0x0B))
OTHER_REPO = RepositoryId(UUID(int=0x0C))
ADMIN = UserId(UUID(int=0xA1))
TRIAGER = UserId(UUID(int=0xA2))
MAIN = GitRef("refs/heads/main")
FEATURE = GitRef("feature/x")


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def seed_tenancy(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(t.organizations).values(
                id=ORG.value, github_installation_id=42, account_login="acme", status="ACTIVE"
            )
        )
        for repo_id, github_id, name in ((REPO, 100, "app"), (OTHER_REPO, 101, "lib")):
            conn.execute(
                insert(t.repositories).values(
                    id=repo_id.value,
                    organization_id=ORG.value,
                    github_repo_id=github_id,
                    owner="acme",
                    name=name,
                    default_branch="main",
                    enabled=True,
                )
            )
        for user, subject in ((ADMIN, "admin"), (TRIAGER, "triager")):
            conn.execute(
                insert(t.users).values(
                    id=user.value, oidc_issuer="https://idp.example", oidc_subject=subject
                )
            )


def scan_id(n: int) -> ScanId:
    return ScanId(UUID(int=0x5000 + n))


def new_scan(
    n: int = 1,
    *,
    repository: RepositoryId = REPO,
    ref: GitRef = MAIN,
    minute: int = 0,
) -> Scan:
    scan, _ = Scan.queue(
        scan_id=scan_id(n),
        repository_id=repository,
        commit=CommitSha(f"{n:040x}"),
        ref=ref,
        trigger=ScanTrigger.MANUAL,
        trigger_key=TriggerKey(f"manual:{n}"),
        at=at(minute),
        requested_by=TRIAGER,
        pull_request_number=7,
    )
    return scan


def fingerprint(n: int) -> Fingerprint:
    return Fingerprint(f"{n:064x}")


def new_finding(
    n: int,
    *,
    scan: ScanId,
    severity: Severity = Severity.HIGH,
    repository: RepositoryId = REPO,
    minute: int = 0,
) -> Finding:
    return Finding.discover(
        finding_id=FindingId(UUID(int=0xF000 + n)),
        repository_id=repository,
        fingerprint=fingerprint(n),
        scanner_kind=ScannerKind("semgrep" if n % 2 else "trivy"),
        category=FindingCategory.SAST,
        rule_id=RuleId(f"rule.{n}"),
        severity=severity,
        title=FindingTitle(f"Finding {n}"),
        scan_id=scan,
        at=at(minute),
        cwe=(CweId("CWE-79"),),
        cve=(CveId("CVE-2024-3094"),),
    )
