"""ADR 0030: administración de plataforma y edición de la política por un ADMIN."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from nave.application.dto.auth import UserSummary
from nave.application.dto.platform import (
    ComponentHealth,
    DeliveryRecord,
    HealthComponent,
    HealthStatus,
    WebhookReason,
)
from nave.domain.value_objects.identifiers import UserId
from nave.domain.value_objects.role import Role
from tests.fakes.auth import Api, TestApp, make_test_app
from tests.unit.application.builders import ORG_ID

BOB = UserId(UUID("00000000-0000-4000-8000-0000000000b0"))
PLATFORM_PATHS = (
    ("GET", "/platform/users"),
    ("GET", "/platform/organizations"),
    ("GET", "/platform/health"),
    ("GET", "/platform/webhooks"),
    ("PUT", f"/platform/users/{BOB}/memberships/{ORG_ID}"),
    ("DELETE", f"/platform/users/{BOB}/memberships/{ORG_ID}"),
    ("DELETE", f"/platform/users/{BOB}/sessions"),
)


def world(*, platform_admin: bool, role: Role | None = Role.VIEWER) -> tuple[TestApp, Api]:
    t = make_test_app()
    api = t.sign_in_as(role)
    if platform_admin:
        t.store.state.platform_admins[t.alice()] = t.clock.now()
    t.store.state.users[BOB] = UserSummary(
        id=BOB, issuer="https://idp.test", display_name="Bob", email=None, last_login_at=None
    )
    return t, api


def call(api: Api, method: str, path: str) -> Any:
    body = {"role": "VIEWER"} if method == "PUT" else None
    return api.call(method, path, json=body)


class TestAccess:
    @pytest.mark.parametrize(("method", "path"), PLATFORM_PATHS)
    def test_non_platform_admins_get_not_found_even_as_org_admins(
        self, method: str, path: str
    ) -> None:
        t, api = world(platform_admin=False, role=Role.ADMIN)
        before = list(t.store.state.audit)
        response = call(api, method, path)
        assert response.status_code == 404
        assert t.store.state.audit == before
        assert (BOB, ORG_ID) not in t.store.state.memberships

    def test_me_tells_whether_the_user_administers_the_platform(self) -> None:
        _, plain = world(platform_admin=False)
        assert plain.get("/me").get_json()["platform_admin"] is False
        _, admin = world(platform_admin=True)
        body = admin.get("/me").get_json()
        assert body["platform_admin"] is True
        assert body["display_name"] == "Alice"

    def test_the_platform_role_gives_no_access_to_organization_data(self) -> None:
        _, api = world(platform_admin=True, role=None)
        assert api.get(f"/organizations/{ORG_ID}/stats").status_code == 404


class TestUsers:
    def test_lists_users_with_memberships_and_live_sessions(self) -> None:
        t, api = world(platform_admin=True)
        items = api.get("/platform/users").get_json()["items"]
        alice = next(i for i in items if i["id"] == str(t.alice()))
        assert alice["platform_admin"] is True
        assert alice["live_sessions"] == 1
        assert alice["memberships"] == [
            {"organization_id": str(ORG_ID), "login": "acme", "role": "VIEWER"}
        ]
        orgs = api.get("/platform/organizations").get_json()["items"]
        assert orgs[0]["login"] == "acme"

    def test_grants_and_removes_roles_with_audit(self) -> None:
        t, api = world(platform_admin=True, role=None)
        put = api.call(
            "PUT", f"/platform/users/{BOB}/memberships/{ORG_ID}", json={"role": "TRIAGER"}
        )
        assert put.status_code == 204
        assert t.store.state.memberships[(BOB, ORG_ID)] is Role.TRIAGER
        entry = t.store.state.audit[-1]
        assert (entry.action, entry.organization_id) == ("membership.changed", ORG_ID)
        assert entry.details["via"] == "platform"
        delete = api.call("DELETE", f"/platform/users/{BOB}/memberships/{ORG_ID}")
        assert delete.status_code == 204
        assert (BOB, ORG_ID) not in t.store.state.memberships

    @pytest.mark.parametrize("role", [None, Role.VIEWER, Role.ADMIN])
    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    def test_cannot_change_own_membership(self, role: Role | None, method: str) -> None:
        # F7-31: darse ADMIN a uno mismo convertiría el rol de plataforma en acceso a datos.
        t, api = world(platform_admin=True, role=role)
        body = {"role": "ADMIN"} if method == "PUT" else None
        path = f"/platform/users/{t.alice()}/memberships/{ORG_ID}"
        assert api.call(method, path, json=body).status_code == 403
        assert t.store.state.memberships.get((t.alice(), ORG_ID)) is role
        entry = t.store.state.audit[-1]
        assert (entry.action, entry.details["reason"]) == ("access.denied", "self")

    def test_never_leaves_an_organization_without_admin(self) -> None:
        t, api = world(platform_admin=True, role=None)
        memberships = t.store.state.memberships
        for key in [k for k, role in memberships.items() if k[1] == ORG_ID and role is Role.ADMIN]:
            del memberships[key]
        memberships[(BOB, ORG_ID)] = Role.ADMIN
        response = api.call("DELETE", f"/platform/users/{BOB}/memberships/{ORG_ID}")
        assert response.status_code == 409
        assert t.store.state.memberships[(BOB, ORG_ID)] is Role.ADMIN

    def test_unknown_user_or_organization_is_not_found(self) -> None:
        _, api = world(platform_admin=True)
        ghost = "00000000-0000-4000-8000-0000000000ee"
        assert (
            api.call("PUT", f"/platform/users/{ghost}/memberships/{ORG_ID}", json={"role": "ADMIN"})
        ).status_code == 404
        assert (
            api.call("PUT", f"/platform/users/{BOB}/memberships/{ghost}", json={"role": "ADMIN"})
        ).status_code == 404

    def test_closes_every_session_of_a_user(self) -> None:
        t = make_test_app()
        victim = t.sign_in_as(Role.VIEWER)
        admin = t.sign_in_as(Role.VIEWER)
        t.store.state.platform_admins[t.alice()] = t.clock.now()
        # Ambas sesiones son de Alice: cerrar las suyas también cierra la del administrador.
        body = admin.call("DELETE", f"/platform/users/{t.alice()}/sessions").get_json()
        assert body == {"revoked": 2}
        assert victim.get("/me").status_code == 401
        assert t.store.state.audit[-1].action == "session.revoked_by_platform"


class TestHealth:
    def report(self, t: TestApp, component: HealthComponent, age: timedelta) -> ComponentHealth:
        return ComponentHealth.of(component, HealthStatus.OK, "ok", t.clock.now() - age, 5)

    def test_fresh_heartbeat_reports_every_component(self) -> None:
        t, api = world(platform_admin=True)
        for component in (
            HealthComponent.WORKER,
            HealthComponent.SCAN_RUNNER,
            HealthComponent.EGRESS_PROXY,
        ):
            t.store.state.health[component] = self.report(t, component, timedelta(seconds=30))
        body = api.get("/platform/health").get_json()
        assert body["stale"] is False
        statuses = {c["component"]: c["status"] for c in body["components"]}
        assert statuses["scan-runner"] == "OK"
        assert statuses["database"] == "OK"
        assert [c["component"] for c in body["components"]][:2] == ["database", "broker"]

    def test_a_stale_heartbeat_marks_what_only_the_worker_sees_as_down(self) -> None:
        t, api = world(platform_admin=True)
        for component in (HealthComponent.WORKER, HealthComponent.SCAN_RUNNER):
            t.store.state.health[component] = self.report(t, component, timedelta(minutes=10))
        body = api.get("/platform/health").get_json()
        assert body["stale"] is True
        statuses = {c["component"]: c["status"] for c in body["components"]}
        assert statuses["scan-runner"] == "DOWN"
        assert statuses["egress-proxy"] == "DOWN"
        assert statuses["database"] == "OK"

    def test_no_heartbeat_at_all_is_stale(self) -> None:
        _, api = world(platform_admin=True)
        body = api.get("/platform/health").get_json()
        assert (body["stale"], body["heartbeat_at"]) == (True, None)


class TestWebhooks:
    def test_deliveries_outcomes_and_counters_of_the_last_day(self) -> None:
        t, api = world(platform_admin=True)
        now = t.clock.now()
        delivery = "0a1b2c3d-0000-4000-8000-000000000001"
        t.store.state.delivery_log.append(DeliveryRecord(delivery, "push", "ENQUEUED", now))
        old = now - timedelta(days=3)
        t.store.state.delivery_log.append(
            DeliveryRecord("0a1b2c3d-0000-4000-8000-000000000002", "push", "IGNORED", old)
        )
        minute = now.replace(second=0, microsecond=0)
        t.store.state.webhook_counters[(minute, WebhookReason.SIGNATURE)] = 4
        t.store.state.webhook_counters[(old, WebhookReason.DUPLICATE)] = 9
        body = api.get("/platform/webhooks").get_json()
        assert body["recent"][0]["delivery_id"] == delivery
        assert body["outcomes"] == [{"outcome": "ENQUEUED", "count": 1}]
        assert [(c["reason"], c["count"]) for c in body["counters"]] == [("SIGNATURE", 4)]


class TestPolicyEditing:
    POLICY: dict[str, Any] = {  # noqa: RUF012 - constante de test
        "gates": [
            {
                "id": "no-new-high",
                "scope": "NEW_FINDINGS",
                "max": [{"severity": "HIGH", "value": 0}],
            }
        ],
        "sla_days": [{"severity": "CRITICAL", "value": 7}],
        "fail_on_sla_breach": True,
    }

    def test_each_save_publishes_a_new_version_with_audit(self) -> None:
        t, api = world(platform_admin=False, role=Role.ADMIN)
        first = api.call("PUT", f"/organizations/{ORG_ID}/policy", json=self.POLICY)
        assert (first.status_code, first.get_json()) == (201, {"version": 1})
        second = api.call("PUT", f"/organizations/{ORG_ID}/policy", json=self.POLICY)
        assert second.get_json() == {"version": 2}
        record = t.store.state.policies[ORG_ID]
        assert record.version == 2
        assert record.policy.fail_on_sla_breach is True
        entry = t.store.state.audit[-1]
        assert (entry.action, entry.details["version"]) == ("policy.changed", "2")
        (shown,) = api.get(f"/organizations/{ORG_ID}/policy").get_json()["items"]
        assert shown["gates"][0]["id"] == "no-new-high"

    def test_only_admins_edit_it(self) -> None:
        t, api = world(platform_admin=True, role=Role.TRIAGER)
        response = api.call("PUT", f"/organizations/{ORG_ID}/policy", json=self.POLICY)
        assert response.status_code == 403
        assert ORG_ID not in t.store.state.policies

    @pytest.mark.parametrize(
        "change",
        [
            {
                "gates": [
                    {
                        "id": "Bad Id",
                        "scope": "NEW_FINDINGS",
                        "max": [{"severity": "HIGH", "value": 0}],
                    }
                ]
            },
            {"gates": [{"id": "a", "scope": "NEW_FINDINGS", "max": []}]},
            {
                "gates": [
                    {"id": "a", "scope": "SOMETIMES", "max": [{"severity": "HIGH", "value": 0}]}
                ]
            },
            {
                "gates": [
                    {"id": "a", "scope": "ALL_OPEN", "max": [{"severity": "HIGH", "value": 0}]},
                    {"id": "a", "scope": "ALL_OPEN", "max": [{"severity": "LOW", "value": 0}]},
                ]
            },
            {"sla_days": [{"severity": "HIGH", "value": 0}]},
            {"sla_days": [{"severity": "HIGH", "value": 9999}]},
            {"sla_days": [{"severity": "HIGH", "value": 3}, {"severity": "HIGH", "value": 5}]},
            {"fail_on_sla_breach": "yes"},
            {"extra": 1},
        ],
    )
    def test_rejects_invalid_policies(self, change: dict[str, Any]) -> None:
        t, api = world(platform_admin=False, role=Role.ADMIN)
        response = api.call(
            "PUT", f"/organizations/{ORG_ID}/policy", json={**self.POLICY, **change}
        )
        assert response.status_code == 400
        assert ORG_ID not in t.store.state.policies
