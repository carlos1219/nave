"""API REST de negocio: contratos, validación, roles y paginación (ADR 0017)."""

from datetime import timedelta
from typing import Any

import pytest

from nave.delivery.http.api_models import SeverityCountsOut
from nave.delivery.http.problems import PROBLEM_JSON
from nave.domain.entities.tenancy import Organization, OrganizationStatus
from nave.domain.value_objects.identifiers import OrganizationId
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.triage_state import TriageState
from tests.fakes.auth import Api, TestApp, make_test_app
from tests.unit.application.builders import ADMIN, ORG_ID, REPO_ID, TRIAGER, VIEWER
from tests.unit.application.test_use_cases import request_id, run, set_policy, start

COMMIT = "a" * 40
IDEMPOTENCY = "Idempotency-Key"
PROBLEM_KEYS = {"type", "title", "status", "request_id"}
NO_FINDINGS = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}


def world(role: Role | None = Role.ADMIN) -> tuple[TestApp, Api]:
    t = make_test_app()
    return t, t.sign_in_as(role)


def scan_body(commit: str = COMMIT, ref: str = "refs/heads/main") -> dict[str, str]:
    return {"commit": commit, "ref": ref}


def assert_problem(response: Any, status: int) -> None:
    assert response.status_code == status
    assert response.mimetype == PROBLEM_JSON
    assert set(response.get_json()) == PROBLEM_KEYS


class TestOrganizations:
    def test_lists_only_the_users_organizations_with_the_role(self) -> None:
        t, api = world(Role.TRIAGER)
        other = OrganizationId.parse("00000000-0000-4000-8000-00000000c002")
        t.store.state.organizations[other] = Organization(
            id=other,
            github_installation_id=5,
            account_login="zeta",
            status=OrganizationStatus.ACTIVE,
        )
        body = api.get("/organizations").get_json()
        assert body == {
            "items": [
                {
                    "id": str(ORG_ID),
                    "login": "acme",
                    "status": "ACTIVE",
                    "role": "TRIAGER",
                    "open_findings": NO_FINDINGS,
                }
            ]
        }

    def test_uninstalled_organizations_disappear(self) -> None:
        t, api = world()
        org = t.store.state.organizations[ORG_ID]
        t.store.state.organizations[ORG_ID] = Organization(
            id=org.id,
            github_installation_id=org.github_installation_id,
            account_login=org.account_login,
            status=OrganizationStatus.UNINSTALLED,
        )
        assert api.get("/organizations").get_json() == {"items": []}

    def test_a_user_without_memberships_gets_an_empty_list_not_an_error(self) -> None:
        _, api = world(None)
        assert api.get("/organizations").get_json() == {"items": []}

    def test_repositories(self) -> None:
        _, api = world(Role.VIEWER)
        body = api.get(f"/organizations/{ORG_ID}/repositories").get_json()
        assert body["items"] == [
            {
                "id": str(REPO_ID),
                "owner": "acme",
                "name": "api",
                "default_branch": "main",
                "enabled": True,
                "open_findings": NO_FINDINGS,
            }
        ]

    def test_open_findings_are_counted_per_organization_and_repository(self) -> None:
        t, api = world(Role.VIEWER)
        run(t.harness, start(t.harness))
        findings = list(t.store.state.findings.values())
        assert findings
        expected = SeverityCountsOut.of(
            SeverityCounts.of(f.severity for f in findings if f.triage_state is TriageState.OPEN)
        ).model_dump()
        assert sum(expected.values()) > 0
        (org,) = api.get("/organizations").get_json()["items"]
        assert org["open_findings"] == expected
        (repo,) = api.get(f"/organizations/{ORG_ID}/repositories").get_json()["items"]
        assert repo["open_findings"] == expected

    def test_non_members_get_404(self) -> None:
        _, api = world(None)
        assert_problem(api.get(f"/organizations/{ORG_ID}/repositories"), 404)


class TestMembers:
    def test_only_admins_list_members_with_profiles(self) -> None:
        t, api = world(Role.ADMIN)
        body = api.get(f"/organizations/{ORG_ID}/members").get_json()
        by_id = {m["user_id"]: m for m in body["items"]}
        assert by_id[str(VIEWER)]["role"] == "VIEWER"
        assert by_id[str(VIEWER)]["display_name"] == "Vic"
        assert by_id[str(t.alice())]["role"] == "ADMIN"
        assert body["items"][0]["role"] == "ADMIN"  # administradores primero

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.TRIAGER])
    def test_other_roles_are_forbidden(self, role: Role) -> None:
        _, api = world(role)
        assert_problem(api.get(f"/organizations/{ORG_ID}/members"), 403)

    def test_set_role_changes_membership_and_audits_the_actor(self) -> None:
        t, api = world(Role.ADMIN)
        response = api.call(
            "PUT", f"/organizations/{ORG_ID}/members/{VIEWER}", json={"role": "TRIAGER"}
        )
        assert response.status_code == 204
        assert response.get_data() == b""
        assert t.store.state.memberships[(VIEWER, ORG_ID)] is Role.TRIAGER
        entry = t.store.state.audit[-1]
        assert (entry.action, entry.actor) == ("membership.changed", t.alice())

    def test_remove_member(self) -> None:
        t, api = world(Role.ADMIN)
        assert api.call("DELETE", f"/organizations/{ORG_ID}/members/{VIEWER}").status_code == 204
        assert (VIEWER, ORG_ID) not in t.store.state.memberships

    def test_cannot_remove_the_last_admin(self) -> None:
        t, api = world(Role.ADMIN)
        del t.store.state.memberships[(ADMIN, ORG_ID)]  # Alice queda como única administradora
        response = api.call("DELETE", f"/organizations/{ORG_ID}/members/{t.alice()}")
        assert_problem(response, 409)

    def test_unknown_user_is_404(self) -> None:
        _, api = world(Role.ADMIN)
        ghost = "00000000-0000-4000-8000-0000000000ff"
        response = api.call(
            "PUT", f"/organizations/{ORG_ID}/members/{ghost}", json={"role": "VIEWER"}
        )
        assert_problem(response, 404)

    @pytest.mark.parametrize(
        "body",
        [{"role": "OWNER"}, {"role": "viewer"}, {}, {"role": "VIEWER", "x": 1}, {"role": 1}, []],
    )
    def test_invalid_bodies_are_400(self, body: object) -> None:
        _, api = world(Role.ADMIN)
        assert_problem(api.call("PUT", f"/organizations/{ORG_ID}/members/{VIEWER}", json=body), 400)

    def test_triagers_cannot_change_roles(self) -> None:
        t, api = world(Role.TRIAGER)
        response = api.call(
            "PUT", f"/organizations/{ORG_ID}/members/{VIEWER}", json={"role": "ADMIN"}
        )
        assert_problem(response, 403)
        assert t.store.state.memberships[(VIEWER, ORG_ID)] is Role.VIEWER


class TestStartScan:
    def test_creates_and_is_idempotent(self) -> None:
        t, api = world(Role.TRIAGER)
        key = {IDEMPOTENCY: request_id(7)}
        first = api.call("POST", f"/repositories/{REPO_ID}/scans", json=scan_body(), headers=key)
        assert first.status_code == 202
        scan_id = first.get_json()["scan_id"]
        assert first.get_json()["created"] is True
        assert first.headers["Location"] == f"/api/v1/scans/{scan_id}"
        second = api.call("POST", f"/repositories/{REPO_ID}/scans", json=scan_body(), headers=key)
        assert second.status_code == 200
        assert second.get_json() == {"scan_id": scan_id, "created": False}
        assert len(t.store.state.scans) == 1

    def test_the_actor_is_the_session_user_never_the_body(self) -> None:
        t, api = world(Role.TRIAGER)
        body = {**scan_body(), "actor": str(TRIAGER), "requested_by": str(TRIAGER)}
        assert_problem(
            api.call(
                "POST",
                f"/repositories/{REPO_ID}/scans",
                json=body,
                headers={IDEMPOTENCY: request_id(1)},
            ),
            400,
        )
        api.call(
            "POST",
            f"/repositories/{REPO_ID}/scans",
            json=scan_body(),
            headers={IDEMPOTENCY: request_id(2)},
        )
        (scan,) = t.store.state.scans.values()
        assert scan.requested_by == t.alice()

    @pytest.mark.parametrize(
        "headers",
        [{}, {IDEMPOTENCY: "not-a-uuid"}, {IDEMPOTENCY: "0A1B2C3D-0000-4000-8000-000000000001"}],
    )
    def test_idempotency_key_is_required_and_canonical(self, headers: dict[str, str]) -> None:
        _, api = world(Role.TRIAGER)
        assert_problem(
            api.call("POST", f"/repositories/{REPO_ID}/scans", json=scan_body(), headers=headers),
            400,
        )

    @pytest.mark.parametrize(
        "body",
        [
            {"commit": "abc", "ref": "main"},
            {"commit": "A" * 40, "ref": "main"},
            {"commit": COMMIT, "ref": ""},
            {"commit": COMMIT, "ref": "-x"},
            {"commit": COMMIT},
            {"ref": "main"},
            {"commit": COMMIT, "ref": "a" * 300},
            {"commit": 5, "ref": "main"},
            {"commit": COMMIT, "ref": "main", "extra": True},
        ],
    )
    def test_bad_bodies_are_400_and_create_nothing(self, body: dict[str, Any]) -> None:
        t, api = world(Role.TRIAGER)
        response = api.call(
            "POST",
            f"/repositories/{REPO_ID}/scans",
            json=body,
            headers={IDEMPOTENCY: request_id(1)},
        )
        assert_problem(response, 400)
        assert t.store.state.scans == {}

    def test_viewers_cannot_start_scans(self) -> None:
        t, api = world(Role.VIEWER)
        response = api.call(
            "POST",
            f"/repositories/{REPO_ID}/scans",
            json=scan_body(),
            headers={IDEMPOTENCY: request_id(1)},
        )
        assert_problem(response, 403)
        assert t.store.state.scans == {}

    def test_disabled_repositories_conflict(self) -> None:
        t, api = world(Role.TRIAGER)
        from dataclasses import replace  # noqa: PLC0415

        t.store.state.repositories[REPO_ID] = replace(
            t.store.state.repositories[REPO_ID], enabled=False
        )
        response = api.call(
            "POST",
            f"/repositories/{REPO_ID}/scans",
            json=scan_body(),
            headers={IDEMPOTENCY: request_id(1)},
        )
        assert_problem(response, 409)


class TestBodyHandling:
    URL = f"/organizations/{ORG_ID}/members/{VIEWER}"

    def test_wrong_content_type(self) -> None:
        _, api = world(Role.ADMIN)
        response = api.call("PUT", self.URL, data=b'{"role":"VIEWER"}', content_type="text/plain")
        assert_problem(response, 400)

    @pytest.mark.parametrize(
        "raw",
        [b'{"role":"VIEWER","role":"ADMIN"}', b'{"role":NaN}', b"{", b"", b"null", b"[1]"],
    )
    def test_malformed_or_ambiguous_json(self, raw: bytes) -> None:
        t, api = world(Role.ADMIN)
        response = api.call("PUT", self.URL, data=raw, content_type="application/json")
        assert_problem(response, 400)
        assert t.store.state.memberships[(VIEWER, ORG_ID)] is Role.VIEWER

    def test_oversized_bodies(self) -> None:
        _, api = world(Role.ADMIN)
        raw = b'{"role":"VIEWER","pad":"' + b"x" * 40_000 + b'"}'
        assert_problem(api.call("PUT", self.URL, data=raw, content_type="application/json"), 400)

    def test_ids_must_be_canonical_uuids(self) -> None:
        _, api = world(Role.ADMIN)
        for bad in ("nope", str(ORG_ID).upper(), str(ORG_ID).replace("-", "")):
            assert_problem(api.get(f"/organizations/{bad}/members"), 400)


class TestScans:
    def make_scans(self, t: TestApp, count: int) -> list[str]:
        ids = []
        for n in range(1, count + 1):
            t.harness.clock.advance(minutes=1)
            ids.append(str(start(t.harness, n=n, commit=_commit(n))))
        return ids

    def test_get_scan_exposes_status_and_summary_after_a_run(self) -> None:
        t, api = world(Role.VIEWER)
        scan_id = start(t.harness)
        run(t.harness, scan_id)
        body = api.get(f"/scans/{scan_id}").get_json()
        assert body["status"] == "COMPLETED"
        assert body["repository_id"] == str(REPO_ID)
        assert body["summary"]["verdict"] in {"PASS", "FAIL", "NEUTRAL"}
        assert body["summary"]["open"]["high"] == 1
        assert body["status_reason"] is None
        assert "version" not in body
        assert "trigger_key" not in body

    def test_list_is_newest_first_and_paginated_with_stable_cursors(self) -> None:
        t, api = world(Role.VIEWER)
        ids = self.make_scans(t, 5)
        page1 = api.get(f"/repositories/{REPO_ID}/scans?limit=2").get_json()
        assert [s["id"] for s in page1["items"]] == ids[::-1][:2]
        assert page1["next_cursor"]
        page2 = api.get(
            f"/repositories/{REPO_ID}/scans?limit=2&cursor={page1['next_cursor']}"
        ).get_json()
        assert [s["id"] for s in page2["items"]] == ids[::-1][2:4]
        page3 = api.get(
            f"/repositories/{REPO_ID}/scans?limit=2&cursor={page2['next_cursor']}"
        ).get_json()
        assert [s["id"] for s in page3["items"]] == ids[::-1][4:]
        assert page3["next_cursor"] is None

    @pytest.mark.parametrize(
        "query",
        ["limit=0", "limit=201", "limit=x", "limit=1&limit=2", "cursor=!!", "cursor=AAAA", "x=1"],
    )
    def test_bad_pagination_is_400(self, query: str) -> None:
        _, api = world(Role.VIEWER)
        assert_problem(api.get(f"/repositories/{REPO_ID}/scans?{query}"), 400)

    def test_cancel(self) -> None:
        t, api = world(Role.TRIAGER)
        scan_id = start(t.harness)
        assert api.call("POST", f"/scans/{scan_id}/cancel").get_json() == {"cancelled": True}
        assert api.call("POST", f"/scans/{scan_id}/cancel").get_json() == {"cancelled": False}
        assert api.get(f"/scans/{scan_id}").get_json()["status"] == "CANCELLED"

    def test_viewers_cannot_cancel(self) -> None:
        t, api = world(Role.VIEWER)
        scan_id = start(t.harness)
        assert_problem(api.call("POST", f"/scans/{scan_id}/cancel"), 403)

    def test_logs_resume_after_a_sequence_number(self) -> None:
        t, api = world(Role.VIEWER)
        scan_id = start(t.harness)
        run(t.harness, scan_id)
        everything = api.get(f"/scans/{scan_id}/logs").get_json()["items"]
        assert len(everything) >= 2
        assert [e["seq"] for e in everything] == sorted(e["seq"] for e in everything)
        later = api.get(
            f"/scans/{scan_id}/logs?after_seq={everything[0]['seq']}&limit=1"
        ).get_json()
        assert later["items"] == everything[1:2]
        assert set(everything[0]) == {"seq", "at", "level", "message"}

    @pytest.mark.parametrize(
        "query", ["after_seq=-1", "after_seq=x", "limit=501", "limit=0", "y=1"]
    )
    def test_bad_log_parameters(self, query: str) -> None:
        t, api = world(Role.VIEWER)
        scan_id = start(t.harness)
        assert_problem(api.get(f"/scans/{scan_id}/logs?{query}"), 400)

    def test_quality_gate_before_and_after_evaluation(self) -> None:
        t, api = world(Role.VIEWER)
        set_policy(t.harness)
        scan_id = start(t.harness)
        before = api.get(f"/scans/{scan_id}/quality-gate").get_json()
        assert before == {"evaluated": False, "verdict": None, "reasons": []}
        run(t.harness, scan_id)
        after = api.get(f"/scans/{scan_id}/quality-gate").get_json()
        assert after["evaluated"] is True
        assert after["verdict"] == "FAIL"
        assert after["reasons"][0]["rule"] == "no-new-high"
        assert after["reasons"][0]["blocking"] is True


def _commit(n: int) -> Any:
    from nave.domain.value_objects.commit_sha import CommitSha  # noqa: PLC0415

    return CommitSha(f"{n:040x}")


class TestFindings:
    def completed(self) -> tuple[TestApp, Api, str]:
        t, api = world(Role.TRIAGER)
        scan_id = start(t.harness)
        run(t.harness, scan_id)
        return t, api, str(scan_id)

    def test_list_and_filters(self) -> None:
        _, api, scan_id = self.completed()
        page = api.get(f"/repositories/{REPO_ID}/findings").get_json()
        (item,) = page["items"]
        assert item["severity"] == "HIGH"
        assert item["scanner"] == "semgrep"
        assert item["triage_state"] == "OPEN"
        assert item["occurrence"] is None
        assert api.get(f"/repositories/{REPO_ID}/findings?severity=LOW").get_json()["items"] == []
        both = api.get(f"/repositories/{REPO_ID}/findings?severity=LOW&severity=HIGH").get_json()
        assert len(both["items"]) == 1
        seen = api.get(
            f"/repositories/{REPO_ID}/findings?seen_in_scan={scan_id}&only_new=true"
        ).get_json()
        occurrence = seen["items"][0]["occurrence"]
        assert occurrence["is_new"] is True
        assert occurrence["location"]["path"] == "app/run.py"
        assert occurrence["snippet"]

    @pytest.mark.parametrize(
        "query",
        [
            "severity=URGENT",
            "state=done",
            "scanner=BAD_NAME",
            "only_new=true",
            "only_new=yes",
            "seen_in_scan=nope",
            "limit=999",
            "sort=asc",
            "severity=LOW&" * 9,
        ],
    )
    def test_bad_filters_are_400(self, query: str) -> None:
        _, api, _ = self.completed()
        assert_problem(api.get(f"/repositories/{REPO_ID}/findings?{query}".rstrip("&")), 400)

    def test_responses_never_expose_fingerprints(self) -> None:
        t, api, _ = self.completed()
        (finding,) = t.store.state.findings.values()
        text = api.get(f"/repositories/{REPO_ID}/findings").get_data(as_text=True)
        text += api.get(f"/findings/{finding.id}").get_data(as_text=True)
        assert finding.fingerprint.value not in text
        assert "fingerprint" not in text

    def test_detail_includes_the_triage_history(self) -> None:
        t, api, _ = self.completed()
        (finding,) = t.store.state.findings.values()
        response = api.call(
            "POST",
            f"/findings/{finding.id}/triage",
            json={
                "state": "ACCEPTED_RISK",
                "justification": "Mitigated by WAF rule 1234 until Q1 refactor.",
                "expires_at": (t.clock.now() + timedelta(days=30)).isoformat(),
            },
        )
        assert response.status_code == 201
        assert response.headers["Location"] == f"/api/v1/findings/{finding.id}"
        assert response.get_json()["state"] == "ACCEPTED_RISK"
        detail = api.get(f"/findings/{finding.id}").get_json()
        assert detail["triage_state"] == "ACCEPTED_RISK"
        assert detail["occurrence"]["message"]
        (decision,) = detail["decisions"]
        assert decision["decided_by"] == str(t.alice())
        assert decision["justification"].startswith("Mitigated")

    @pytest.mark.parametrize(
        "body",
        [
            {"state": "ACCEPTED_RISK"},
            {"state": "FALSE_POSITIVE"},
            {"state": "FALSE_POSITIVE", "justification": "short"},
            {
                "state": "ACCEPTED_RISK",
                "justification": "x" * 20,
                "expires_at": "2030-01-01T00:00:00",
            },
            {"state": "ACCEPTED_RISK", "justification": "x" * 20, "expires_at": "yesterday"},
            {"state": "OPEN", "expires_at": "2030-01-01T00:00:00Z"},
            {"state": "DONE"},
            {"state": "OPEN", "decided_by": str(VIEWER)},
            {},
        ],
    )
    def test_invalid_triage_is_400_and_records_nothing(self, body: dict[str, Any]) -> None:
        t, api, _ = self.completed()
        (finding,) = t.store.state.findings.values()
        assert_problem(api.call("POST", f"/findings/{finding.id}/triage", json=body), 400)
        assert t.store.state.decisions == []

    def test_viewers_cannot_triage(self) -> None:
        t, _, _ = self.completed()
        viewer = t.sign_in_as(Role.VIEWER)
        (finding,) = t.store.state.findings.values()
        response = viewer.call("POST", f"/findings/{finding.id}/triage", json={"state": "OPEN"})
        assert_problem(response, 403)

    def test_triage_requires_csrf(self) -> None:
        t, api, _ = self.completed()
        (finding,) = t.store.state.findings.values()
        bad = api.client.post(
            f"/api/v1/findings/{finding.id}/triage",
            json={"state": "OPEN"},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": api.csrf},
            base_url="https://nave.test",
        )
        assert_problem(bad, 403)
        assert t.store.state.decisions == []


def test_unauthenticated_requests_are_401_everywhere() -> None:
    t = make_test_app()
    client = t.browser()
    for path in (
        "/organizations",
        f"/organizations/{ORG_ID}/repositories",
        f"/repositories/{REPO_ID}/scans",
        f"/repositories/{REPO_ID}/findings",
        "/openapi.json",
    ):
        assert_problem(client.get(f"/api/v1{path}", base_url="https://nave.test"), 401)
