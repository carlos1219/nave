"""Escenario de tenancy y salidas de escáner para tests de aplicación."""

from datetime import timedelta
from uuid import UUID

from nave.application.dto.auth import UserSummary
from nave.application.dto.scanning import FetchLimits, NormalizedResult, ScanLimits, ScannerOutput
from nave.application.services.fingerprinting import FingerprintKey
from nave.domain.entities.tenancy import Organization, OrganizationStatus, Repository
from nave.domain.value_objects.finding_category import FindingCategory
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import OrganizationId, RepositoryId, UserId
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity import Severity
from tests.fakes.memory import Store

TEST_KEY = FingerprintKey(bytes(range(32)))
ORG_ID = OrganizationId(UUID("00000000-0000-4000-8000-00000000c001"))
OTHER_ORG_ID = OrganizationId(UUID("00000000-0000-4000-8000-00000000c002"))
REPO_ID = RepositoryId(UUID("00000000-0000-4000-8000-00000000a001"))
ADMIN = UserId(UUID("00000000-0000-4000-8000-00000000b001"))
TRIAGER = UserId(UUID("00000000-0000-4000-8000-00000000b002"))
VIEWER = UserId(UUID("00000000-0000-4000-8000-00000000b003"))
OUTSIDER = UserId(UUID("00000000-0000-4000-8000-00000000b004"))
INSTALLATION_ID = 777
GITHUB_REPO_ID = 4242
DIGEST = "sha256:" + "d" * 64
OUTPUT_SHA = "e" * 64

SCAN_LIMITS = ScanLimits(
    max_duration=timedelta(minutes=15), max_output_bytes=50 << 20, max_results=50_000
)
FETCH_LIMITS = FetchLimits(max_bytes=1 << 30, max_files=200_000, max_duration=timedelta(minutes=5))


def seed_tenancy(store: Store, *, enabled: bool = True) -> None:
    state = store.state
    state.organizations[ORG_ID] = Organization(
        id=ORG_ID,
        github_installation_id=INSTALLATION_ID,
        account_login="acme",
        status=OrganizationStatus.ACTIVE,
    )
    state.repositories[REPO_ID] = Repository(
        id=REPO_ID,
        organization_id=ORG_ID,
        github_repo_id=GITHUB_REPO_ID,
        ref=RepoRef("acme", "api"),
        default_branch=GitRef("main"),
        enabled=enabled,
    )
    state.memberships[(ADMIN, ORG_ID)] = Role.ADMIN
    state.memberships[(TRIAGER, ORG_ID)] = Role.TRIAGER
    state.memberships[(VIEWER, ORG_ID)] = Role.VIEWER


def sast(  # noqa: PLR0913 - constructor de datos de prueba
    rule: str = "python.lang.security.audit.subprocess-shell-true",
    *,
    path: str | None = "app/run.py",
    line: int | None = 10,
    snippet: str | None = "subprocess.run(cmd, shell=True)",
    severity: Severity = Severity.HIGH,
    message: str = "Avoid shell=True",
) -> NormalizedResult:
    return NormalizedResult(
        rule_id=rule,
        category=FindingCategory.SAST,
        severity=severity,
        title="Subprocess with shell=True",
        message=message,
        path=path,
        start_line=line,
        end_line=line,
        snippet=snippet,
        cwe=("CWE-78",),
    )


def output(
    *results: NormalizedResult, kind: str = "semgrep", truncated: bool = False
) -> ScannerOutput:
    return ScannerOutput(
        kind=ScannerKind(kind),
        scanner_version="1.2.3",
        image_digest=DIGEST,
        output_sha256=OUTPUT_SHA,
        results=results,
        truncated=truncated,
    )


def seed_users(store: Store) -> None:
    """Perfiles de los usuarios del escenario (la gestión de miembros exige que existan)."""
    for user, name in ((ADMIN, "Ada"), (TRIAGER, "Tris"), (VIEWER, "Vic"), (OUTSIDER, "Otto")):
        store.state.users[user] = UserSummary(
            id=user,
            issuer="https://idp.test",
            subject=f"sub-{name.lower()}",
            display_name=name,
            email=f"{name.lower()}@example.com",
            last_login_at=None,
        )
