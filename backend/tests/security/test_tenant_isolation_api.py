"""AM-106 sobre la API: lo de otra organización es indistinguible de lo inexistente."""

from dataclasses import dataclass
from typing import Any

import pytest

from nave.application.use_cases.start_scan import StartScanCommand
from nave.domain.entities.tenancy import Organization, OrganizationStatus, Repository
from nave.domain.value_objects.commit_sha import CommitSha
from nave.domain.value_objects.git_ref import GitRef
from nave.domain.value_objects.identifiers import OrganizationId, RepositoryId, UserId
from nave.domain.value_objects.repo_ref import RepoRef
from nave.domain.value_objects.role import Role
from tests.fakes.auth import Api, TestApp, make_test_app
from tests.unit.application.builders import ORG_ID
from tests.unit.application.test_use_cases import run

OTHER_ORG = OrganizationId.parse("00000000-0000-4000-8000-0000000000d1")
OTHER_REPO = RepositoryId.parse("00000000-0000-4000-8000-0000000000d2")
BOB = UserId.parse("00000000-0000-4000-8000-0000000000d3")
GHOST = "00000000-0000-4000-8000-0000000000ee"
COMMIT = "b" * 40


@dataclass
class Scene:
    t: TestApp
    api: Api
    scan: str
    finding: str


def scene(role: Role | None = Role.ADMIN) -> Scene:
    """Alice pertenece a `acme`; `globex` tiene un repositorio, un escaneo y un finding."""
    t = make_test_app()
    state = t.store.state
    state.organizations[OTHER_ORG] = Organization(
        id=OTHER_ORG,
        github_installation_id=999,
        account_login="globex",
        status=OrganizationStatus.ACTIVE,
    )
    state.repositories[OTHER_REPO] = Repository(
        id=OTHER_REPO,
        organization_id=OTHER_ORG,
        github_repo_id=9001,
        ref=RepoRef("globex", "vault"),
        default_branch=GitRef("main"),
        enabled=True,
    )
    state.memberships[(BOB, OTHER_ORG)] = Role.ADMIN
    result = t.harness.start.execute(
        StartScanCommand(
            actor=BOB,
            repository_id=OTHER_REPO,
            commit=CommitSha(COMMIT),
            ref=GitRef("refs/heads/main"),
            request_id="00000000-0000-4000-8000-0000000000e1",
        )
    )
    run(t.harness, result.scan_id)
    (finding,) = [f for f in t.store.state.findings.values() if f.repository_id == OTHER_REPO]
    return Scene(t, t.sign_in_as(role, org=ORG_ID), str(result.scan_id), str(finding.id))


def requests(s: Scene, *, foreign: bool) -> list[tuple[str, str, Any, dict[str, str]]]:
    """Todas las operaciones con id, apuntando a lo ajeno o a un id inexistente."""
    org = str(OTHER_ORG) if foreign else GHOST
    repo = str(OTHER_REPO) if foreign else GHOST
    scan = s.scan if foreign else GHOST
    finding = s.finding if foreign else GHOST
    key = {"Idempotency-Key": "00000000-0000-4000-8000-0000000000e2"}
    return [
        ("GET", f"/organizations/{org}/repositories", None, {}),
        ("GET", f"/organizations/{org}/members", None, {}),
        ("PUT", f"/organizations/{org}/members/{BOB}", {"role": "VIEWER"}, {}),
        ("DELETE", f"/organizations/{org}/members/{BOB}", None, {}),
        ("GET", f"/organizations/{org}/stats", None, {}),
        ("GET", f"/organizations/{org}/scans", None, {}),
        ("GET", f"/organizations/{org}/accepted-risks", None, {}),
        ("GET", f"/organizations/{org}/rules", None, {}),
        ("GET", f"/organizations/{org}/dependencies", None, {}),
        ("GET", f"/organizations/{org}/policy", None, {}),
        ("GET", f"/organizations/{org}/audit-log", None, {}),
        ("GET", f"/organizations/{org}/pull-requests", None, {}),
        ("GET", f"/organizations/{org}/triage-queue", None, {}),
        ("GET", f"/organizations/{org}/failed-scans", None, {}),
        ("GET", f"/organizations/{org}/scanners", None, {}),
        ("GET", f"/organizations/{org}/export?view=triage&format=csv", None, {}),
        ("GET", f"/repositories/{repo}/findings/export?format=sarif", None, {}),
        ("GET", f"/repositories/{repo}/scans", None, {}),
        ("POST", f"/repositories/{repo}/scans", {"commit": COMMIT, "ref": "main"}, key),
        ("GET", f"/repositories/{repo}/findings", None, {}),
        ("GET", f"/scans/{scan}", None, {}),
        ("POST", f"/scans/{scan}/cancel", None, {}),
        ("GET", f"/scans/{scan}/logs", None, {}),
        ("GET", f"/scans/{scan}/quality-gate", None, {}),
        ("GET", f"/findings/{finding}", None, {}),
        ("POST", f"/findings/{finding}/triage", {"state": "OPEN"}, {}),
    ]


def shape(response: Any) -> dict[str, Any]:
    body = response.get_json()
    return {k: v for k, v in body.items() if k != "request_id"}


def test_foreign_resources_look_exactly_like_missing_ones_for_every_operation() -> None:
    s = scene(Role.ADMIN)
    foreign, missing = requests(s, foreign=True), requests(s, foreign=False)
    for (method, path, body, headers), (_, ghost_path, _, _) in zip(foreign, missing, strict=True):
        real = s.api.call(method, path, json=body, headers=headers)
        none = s.api.call(method, ghost_path, json=body, headers=headers)
        assert real.status_code == none.status_code == 404, (method, path)
        assert shape(real) == shape(none), (method, path)
        assert real.mimetype == none.mimetype


def test_nothing_changed_in_the_foreign_organization() -> None:
    s = scene(Role.ADMIN)
    before = (
        dict(s.t.store.state.memberships),
        dict(s.t.store.state.scans),
        list(s.t.store.state.decisions),
    )
    for method, path, body, headers in requests(s, foreign=True):
        s.api.call(method, path, json=body, headers=headers)
    after = (
        dict(s.t.store.state.memberships),
        dict(s.t.store.state.scans),
        list(s.t.store.state.decisions),
    )
    assert before == after


def test_probes_of_foreign_organizations_are_logged_not_audited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """F7-16 (ADR 0026): el sondeo de un no miembro no escribe en el audit log (lo podría
    inundar cualquiera y ensuciaría la cadena de otra organización), pero deja un aviso."""
    s = scene(Role.ADMIN)
    before = list(s.t.store.state.audit)
    with caplog.at_level("WARNING", logger="nave.authz"):
        response = s.api.get(f"/scans/{s.scan}")
    assert response.status_code == 404
    assert s.t.store.state.audit == before
    (record,) = caplog.records
    assert record.__dict__["action"] == "access.denied"
    assert record.__dict__["user_id"] == str(s.t.alice())
    assert record.__dict__["organization_id"] == str(OTHER_ORG)


def test_insufficient_role_inside_the_own_organization_is_audited() -> None:
    s = scene(Role.VIEWER)
    response = s.api.call("PUT", f"/organizations/{ORG_ID}/members/{BOB}", json={"role": "VIEWER"})
    assert response.status_code == 403
    entry = s.t.store.state.audit[-1]
    assert (entry.action, entry.actor) == ("access.denied", s.t.alice())


def test_membership_in_one_organization_gives_nothing_in_another() -> None:
    s = scene(Role.ADMIN)
    body = s.api.get("/organizations").get_json()
    assert [o["login"] for o in body["items"]] == ["acme"]


def test_listings_of_own_resources_never_include_foreign_ones() -> None:
    s = scene(Role.ADMIN)
    from tests.unit.application.test_use_cases import start  # noqa: PLC0415

    start(s.t.harness)
    run(s.t.harness, start(s.t.harness, n=2))
    for path in ("/repositories/00000000-0000-4000-8000-00000000a001/scans",):
        text = s.api.get(path).get_data(as_text=True)
        assert s.scan not in text
        assert s.finding not in text
        assert str(OTHER_REPO) not in text


def test_filters_and_exports_of_the_own_organization_ignore_foreign_repositories() -> None:
    """ADR 0029: un `repository_id` ajeno en la cola o en una descarga no amplía el ámbito."""
    s = scene(Role.ADMIN)
    queue = s.api.get(f"/organizations/{ORG_ID}/triage-queue?repository_id={OTHER_REPO}")
    assert queue.get_json() == {"items": [], "next_cursor": None}
    export = s.api.get(
        f"/organizations/{ORG_ID}/export?view=triage&format=csv&repository_id={OTHER_REPO}"
    )
    assert export.status_code == 200
    assert s.finding not in export.get_data(as_text=True)
    assert len(export.get_data(as_text=True).strip().splitlines()) == 1


def test_sessions_of_other_users_cannot_be_closed() -> None:
    s = scene(Role.ADMIN)
    bob_session = "0" * 32
    assert s.api.call("DELETE", f"/me/sessions/{bob_session}").status_code == 404
    assert [i["current"] for i in s.api.get("/me/sessions").get_json()["items"]] == [True]


@pytest.mark.parametrize("role", [Role.VIEWER, Role.TRIAGER])
def test_a_lower_role_in_the_own_organization_is_403_never_a_silent_success(role: Role) -> None:
    s = scene(role)
    response = s.api.call(
        "PUT", f"/organizations/{ORG_ID}/members/{s.t.alice()}", json={"role": "ADMIN"}
    )
    assert response.status_code == 403
    assert s.t.store.state.memberships[(s.t.alice(), ORG_ID)] is role
