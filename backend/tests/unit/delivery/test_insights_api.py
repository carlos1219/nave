"""Vistas por organización: forma de las respuestas, roles y paginación del audit log."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from nave.application.dto.audit import AuditEntry
from nave.application.ports.persistence import PolicyRecord
from nave.domain.quality_gates import Gate, GateScope, QualityGatePolicy
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.triage_state import TriageState
from tests.fakes.auth import Api, TestApp, make_test_app
from tests.unit.application.builders import ORG_ID, REPO_ID
from tests.unit.application.test_use_cases import run, start

PROBLEM_KEYS = {"type", "title", "status", "request_id"}


def world(role: Role | None = Role.VIEWER) -> tuple[TestApp, Api]:
    t = make_test_app()
    return t, t.sign_in_as(role)


def scanned(role: Role = Role.VIEWER) -> tuple[TestApp, Api]:
    t, api = world(role)
    run(t.harness, start(t.harness))
    return t, api


def open_total(counts: dict[str, int]) -> int:
    return sum(counts.values())


class TestStats:
    def test_counts_match_the_open_findings(self) -> None:
        t, api = scanned()
        body = api.get(f"/organizations/{ORG_ID}/stats").get_json()
        expected = sum(
            1 for f in t.store.state.findings.values() if f.triage_state is TriageState.OPEN
        )
        assert expected > 0
        assert open_total(body["open"]) == expected
        assert sum(open_total(r["open"]) for r in body["by_repository"]) == expected
        assert sum(open_total(s["open"]) for s in body["by_scanner"]) == expected
        assert body["by_repository"][0]["repository"]["id"] == str(REPO_ID)
        assert body["top_rules"]
        assert body["top_rules"][0]["open"] >= 1
        assert len(body["trend"]) == 30
        assert body["sla"] == []

    def test_trend_ends_with_the_last_completed_scan(self) -> None:
        t, api = scanned()
        body = api.get(f"/organizations/{ORG_ID}/stats").get_json()
        (scan,) = t.store.state.scans.values()
        assert scan.summary is not None
        assert open_total(body["trend"][-1]["open"]) == scan.summary.open.total

    def test_sla_lists_each_severity_of_the_policy(self) -> None:
        t, api = scanned()
        days = {Severity.CRITICAL: 7, Severity.HIGH: 30, Severity.LOW: 90, Severity.INFO: 120}
        days[Severity.MEDIUM] = 60
        policy = QualityGatePolicy(version=1, gates=(), sla_days=days, fail_on_sla_breach=False)
        t.store.state.policies[ORG_ID] = PolicyRecord(policy_id="p1", version=1, policy=policy)
        body = api.get(f"/organizations/{ORG_ID}/stats").get_json()
        assert [(s["severity"], s["days"], s["overdue"]) for s in body["sla"]] == [
            ("CRITICAL", 7, 0),
            ("HIGH", 30, 0),
            ("MEDIUM", 60, 0),
            ("LOW", 90, 0),
            ("INFO", 120, 0),
        ]
        # Se envejecen los hallazgos y no el reloj: la sesión caducaría.
        findings = t.store.state.findings
        for key, finding in findings.items():
            findings[key] = replace(
                finding, first_seen_at=finding.first_seen_at - timedelta(days=200)
            )
        later = api.get(f"/organizations/{ORG_ID}/stats").get_json()
        assert sum(s["overdue"] for s in later["sla"]) == open_total(later["open"])


class TestLists:
    def test_scans_across_repositories_with_their_repository(self) -> None:
        _, api = scanned()
        (item,) = api.get(f"/organizations/{ORG_ID}/scans").get_json()["items"]
        assert item["scan"]["status"] == "COMPLETED"
        assert item["repository"] == {"id": str(REPO_ID), "owner": "acme", "name": "api"}

    def test_rule_catalog_counts_every_rule_once(self) -> None:
        t, api = scanned()
        items = api.get(f"/organizations/{ORG_ID}/rules").get_json()["items"]
        rules = {f.rule_id.value for f in t.store.state.findings.values()}
        assert {i["rule_id"] for i in items} == rules
        assert sum(i["total"] for i in items) == len(t.store.state.findings)

    def test_accepted_risks_and_dependencies_are_lists_of_findings(self) -> None:
        _, api = scanned()
        for path in ("accepted-risks", "dependencies"):
            body = api.get(f"/organizations/{ORG_ID}/{path}").get_json()
            assert isinstance(body["items"], list)
            for item in body["items"]:
                assert set(item) == {"finding", "repository", "justification"}

    def test_policy_history(self) -> None:
        t, api = world()
        assert api.get(f"/organizations/{ORG_ID}/policy").get_json() == {"items": []}
        policy = QualityGatePolicy(
            version=1,
            gates=(Gate("no-new-high", GateScope.NEW_FINDINGS, {Severity.HIGH: 0}),),
            sla_days={Severity.HIGH: 14},
            fail_on_sla_breach=True,
        )
        t.store.state.policies[ORG_ID] = PolicyRecord(policy_id="p1", version=1, policy=policy)
        (version,) = api.get(f"/organizations/{ORG_ID}/policy").get_json()["items"]
        assert version["active"] is True
        assert version["gates"] == [
            {
                "id": "no-new-high",
                "scope": "NEW_FINDINGS",
                "max": [{"severity": "HIGH", "value": 0}],
            }
        ]
        assert version["sla_days"] == [{"severity": "HIGH", "value": 14}]
        assert version["fail_on_sla_breach"] is True


class TestAuditLog:
    def test_only_admins_read_it(self) -> None:
        _, viewer = scanned(Role.TRIAGER)
        response = viewer.get(f"/organizations/{ORG_ID}/audit-log")
        assert response.status_code == 403
        assert set(response.get_json()) == PROBLEM_KEYS

    def test_newest_first_and_paged_with_an_opaque_cursor(self) -> None:
        t, api = scanned(Role.ADMIN)
        for n in range(2):
            t.store.state.audit.append(
                AuditEntry(
                    occurred_at=t.clock.now(),
                    action="member.role_changed",
                    actor=t.alice(),
                    organization_id=ORG_ID,
                    target_type="user",
                    target_id=f"u{n}",
                    details={"role": "VIEWER"},
                )
            )
        everything = api.get(f"/organizations/{ORG_ID}/audit-log").get_json()
        ids = [e["id"] for e in everything["items"]]
        assert ids == sorted(ids, reverse=True)
        assert len(ids) >= 2
        first = api.get(f"/organizations/{ORG_ID}/audit-log?limit=1").get_json()
        assert [e["id"] for e in first["items"]] == ids[:1]
        assert first["next_cursor"] == str(ids[0])
        second = api.get(
            f"/organizations/{ORG_ID}/audit-log?limit=1&cursor={first['next_cursor']}"
        ).get_json()
        assert [e["id"] for e in second["items"]] == ids[1:2]
        newest = everything["items"][0]
        assert newest["target_id"] == "u1"
        assert newest["details"] == {"role": "VIEWER"}
        assert newest["actor_name"] == "Alice"

    @pytest.mark.parametrize("query", ["cursor=abc", "cursor=", "limit=0", "x=1", "cursor=-1"])
    def test_rejects_bad_parameters(self, query: str) -> None:
        _, api = world(Role.ADMIN)
        assert api.get(f"/organizations/{ORG_ID}/audit-log?{query}").status_code == 400


@pytest.mark.parametrize(
    "path", ["stats", "scans", "accepted-risks", "rules", "dependencies", "policy"]
)
def test_viewers_read_and_unknown_parameters_are_rejected(path: str) -> None:
    _, api = world(Role.VIEWER)
    assert api.get(f"/organizations/{ORG_ID}/{path}").status_code == 200
    bad: Any = api.get(f"/organizations/{ORG_ID}/{path}?limit=1")
    assert bad.status_code == 400
