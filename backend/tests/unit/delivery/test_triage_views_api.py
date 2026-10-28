"""Vistas del ADR 0029: PR, cola de triage, fallidos, escáneres, exportación y sesiones."""

import csv
import io
import json
from dataclasses import replace

import pytest

from nave.domain.value_objects.finding_text import FindingTitle
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import ReasonCode, StatusReason
from nave.domain.value_objects.triage_state import TriageState
from tests.fakes.auth import PUBLIC_ORIGIN, Api, TestApp, make_test_app
from tests.unit.application.builders import ORG_ID, OTHER_ORG_ID, REPO_ID
from tests.unit.application.test_use_cases import run, start

ORG = f"/organizations/{ORG_ID}"


def scanned(role: Role | None = Role.VIEWER) -> tuple[TestApp, Api]:
    t = make_test_app()
    api = t.sign_in_as(role)
    run(t.harness, start(t.harness))
    return t, api


def open_ids(t: TestApp) -> set[str]:
    return {
        str(f.id) for f in t.store.state.findings.values() if f.triage_state is TriageState.OPEN
    }


class TestPullRequests:
    def test_only_scans_of_pull_requests(self) -> None:
        t, api = scanned()
        assert api.get(f"{ORG}/pull-requests").get_json() == {"items": []}
        ((scan_id, scan),) = t.store.state.scans.items()
        t.store.state.scans[scan_id] = replace(scan, pull_request_number=12)
        (item,) = api.get(f"{ORG}/pull-requests").get_json()["items"]
        assert item["scan"]["pull_request_number"] == 12
        assert item["repository"]["name"] == "api"


class TestTriageQueue:
    def test_pages_cover_every_open_finding_once(self) -> None:
        t, api = scanned()
        everything = api.get(f"{ORG}/triage-queue").get_json()
        assert {i["finding"]["id"] for i in everything["items"]} == open_ids(t)
        assert everything["next_cursor"] is None
        seen: list[str] = []
        cursor = None
        while True:
            suffix = f"&cursor={cursor}" if cursor else ""
            page = api.get(f"{ORG}/triage-queue?limit=1{suffix}").get_json()
            seen += [i["finding"]["id"] for i in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == [i["finding"]["id"] for i in everything["items"]]

    def test_filters_by_severity(self) -> None:
        _, api = scanned()
        items = api.get(f"{ORG}/triage-queue?severity=CRITICAL").get_json()["items"]
        assert all(i["finding"]["severity"] == "CRITICAL" for i in items)

    @pytest.mark.parametrize(
        "query",
        ["cursor=nope", "cursor=1_2", "severity=BAD", "repository_id=x", "state=OPEN", "limit=0"],
    )
    def test_rejects_bad_parameters(self, query: str) -> None:
        _, api = scanned()
        assert api.get(f"{ORG}/triage-queue?{query}").status_code == 400


class TestFailedScans:
    def test_lists_failures_with_their_cause(self) -> None:
        t, api = scanned()
        ((scan_id, scan),) = t.store.state.scans.items()
        assert api.get(f"{ORG}/failed-scans").get_json() == {"items": []}
        t.store.state.scans[scan_id] = replace(
            scan,
            status=ScanStatus.FAILED,
            summary=None,
            status_reason=StatusReason(ReasonCode.SCANNER_FAILED, "exit 2"),
        )
        (item,) = api.get(f"{ORG}/failed-scans").get_json()["items"]
        assert item["scan"]["status_reason"] == {"code": "SCANNER_FAILED", "detail": "exit 2"}
        assert item["recovered"] is False


class TestScanners:
    def test_versions_come_from_the_completed_scans(self) -> None:
        _, api = scanned()
        body = api.get(f"{ORG}/scanners").get_json()
        assert body["versions"]
        assert all(v["scans"] == 1 for v in body["versions"])
        assert body["without_tools"] == 0
        (recent,) = body["recent"]
        tools = recent["scan"]["summary"]["tools"]
        assert {t["scanner"] for t in tools} == {v["scanner"] for v in body["versions"]}


class TestExport:
    def test_csv_of_the_triage_queue_neutralizes_formulas(self) -> None:
        t, api = scanned()
        finding = next(
            f for f in t.store.state.findings.values() if f.triage_state is TriageState.OPEN
        )
        t.store.state.findings[finding.id] = replace(finding, title=FindingTitle("=HYPERLINK(1)"))
        response = api.get(f"{ORG}/export?view=triage&format=csv")
        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert response.headers["Content-Disposition"] == 'attachment; filename="nave-triage.csv"'
        assert response.headers["Cache-Control"] == "no-store"
        text = response.get_data().decode("utf-8").removeprefix("﻿")
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0][:3] == ["severity", "triage_state", "scanner"]
        assert len(rows) - 1 == len(open_ids(t))
        assert "'=HYPERLINK(1)" in [r[5] for r in rows[1:]]

    def test_sarif_of_a_repository(self) -> None:
        _, api = scanned()
        response = api.get(f"/repositories/{REPO_ID}/findings/export?format=sarif&state=OPEN")
        assert response.status_code == 200
        assert response.mimetype == "application/sarif+json"
        document = json.loads(response.get_data())
        assert document["version"] == "2.1.0"
        assert int(response.headers["X-Nave-Rows"]) == sum(
            len(run["results"]) for run in document["runs"]
        )

    @pytest.mark.parametrize(
        "query",
        ["view=triage", "view=triage&format=xlsx", "view=repository&format=csv", "format=csv"],
    )
    def test_rejects_bad_parameters(self, query: str) -> None:
        _, api = scanned()
        assert api.get(f"{ORG}/export?{query}").status_code == 400

    def test_strangers_get_not_found(self) -> None:
        _, api = scanned(None)
        assert api.get(f"{ORG}/export?view=triage&format=csv").status_code == 404
        assert api.get(f"/repositories/{REPO_ID}/findings/export?format=csv").status_code == 404
        other = f"/organizations/{OTHER_ORG_ID}/export?view=triage&format=csv"
        assert api.get(other).status_code == 404


class TestMySessions:
    def two_sessions(self) -> tuple[TestApp, Api, Api]:
        t = make_test_app()
        first = t.sign_in_as(Role.VIEWER)
        t.clock.advance(minutes=1)
        second = t.sign_in_as(Role.VIEWER)
        return t, first, second

    def test_lists_own_sessions_and_marks_the_current_one(self) -> None:
        _, first, second = self.two_sessions()
        items = first.get("/me/sessions").get_json()["items"]
        assert len(items) == 2
        assert sum(i["current"] for i in items) == 1
        mine = next(i for i in items if i["current"])
        theirs = next(i for i in second.get("/me/sessions").get_json()["items"] if i["current"])
        assert mine["id"] != theirs["id"]
        assert all(len(i["id"]) == 32 for i in items)

    def test_closing_another_session_signs_it_out_and_is_audited(self) -> None:
        t, first, second = self.two_sessions()
        other = next(i for i in first.get("/me/sessions").get_json()["items"] if not i["current"])
        assert first.call("DELETE", f"/me/sessions/{other['id']}").status_code == 204
        assert second.get("/me/sessions").status_code == 401
        assert t.store.state.audit[-1].action == "session.revoked"
        assert first.call("DELETE", f"/me/sessions/{other['id']}").status_code == 404

    def test_the_current_session_is_closed_with_logout_only(self) -> None:
        _, first, _ = self.two_sessions()
        current = next(i for i in first.get("/me/sessions").get_json()["items"] if i["current"])
        assert first.call("DELETE", f"/me/sessions/{current['id']}").status_code == 400
        assert first.get("/me/sessions").status_code == 200

    def test_close_all_the_others(self) -> None:
        _, first, second = self.two_sessions()
        body = first.call("DELETE", "/me/sessions").get_json()
        assert body == {"revoked": 1}
        assert second.get("/me/sessions").status_code == 401
        assert len(first.get("/me/sessions").get_json()["items"]) == 1

    @pytest.mark.parametrize("session_id", ["ABC", "g" * 32, "0" * 31, "0" * 33])
    def test_rejects_malformed_ids(self, session_id: str) -> None:
        _, first, _ = self.two_sessions()
        assert first.call("DELETE", f"/me/sessions/{session_id}").status_code == 400

    def test_closing_requires_the_csrf_token(self) -> None:
        _, first, _ = self.two_sessions()
        response = first.client.delete(
            "/api/v1/me/sessions", headers={"Origin": PUBLIC_ORIGIN}, base_url=PUBLIC_ORIGIN
        )
        assert response.status_code == 403
