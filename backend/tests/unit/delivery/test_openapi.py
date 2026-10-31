"""El documento OpenAPI es el contrato de la API: no deriva de las rutas ni del dominio."""

import json
import os
import re
from pathlib import Path
from typing import Any, get_args

import pytest

from nave.application.dto.views import LogLevel
from nave.delivery.http import api_models as m
from nave.domain.entities.tenancy import OrganizationStatus
from nave.domain.quality_gates import ReasonKind, Verdict
from nave.domain.value_objects.role import Role
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.triage_state import TriageState
from tests.fakes.auth import make_test_app

CONTRACT = Path(__file__).resolve().parents[4] / "contracts" / "openapi.json"
UNDOCUMENTED = {"/api/v1/webhooks/github", "/api/v1/openapi.json"}
METHODS = {"get", "put", "post", "delete", "patch"}


def served() -> dict[str, Any]:
    t = make_test_app()
    api = t.sign_in_as(None)
    response = api.get("/openapi.json")
    assert response.status_code == 200
    document: dict[str, Any] = response.get_json()
    return document


def test_the_committed_contract_matches_the_code() -> None:
    document = served()
    rendered = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("NAVE_UPDATE_CONTRACTS") == "1":
        CONTRACT.write_text(rendered, encoding="utf-8")
    assert CONTRACT.read_text(encoding="utf-8") == rendered, (
        "contracts/openapi.json está desactualizado: ejecuta los tests con NAVE_UPDATE_CONTRACTS=1 "
        "y revisa el cambio (un cambio de contrato es una decisión, no un efecto secundario)"
    )


def test_every_flask_route_is_documented_and_every_documented_route_exists() -> None:
    t = make_test_app()
    flask_routes: set[tuple[str, str]] = set()
    for rule in t.app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v1") or rule.rule in UNDOCUMENTED:
            continue
        for method in (rule.methods or set()) - {"HEAD", "OPTIONS"}:
            flask_routes.add(
                (method.lower(), re.sub(r"<(\w+)>", r"{\1}", rule.rule.removeprefix("/api/v1")))
            )
    documented = {
        (method, path)
        for path, item in served()["paths"].items()
        for method in item
        if method in METHODS
    }
    assert documented == flask_routes


def test_operation_ids_are_unique_and_security_matches_the_method() -> None:
    seen: set[str] = set()
    for path, item in served()["paths"].items():
        for method, op in item.items():
            assert op["operationId"] not in seen
            seen.add(op["operationId"])
            security = op["security"]
            if path in {"/auth/login", "/auth/callback"}:
                assert security == []
            elif method == "get":
                assert security == [{"sessionCookie": []}]
            else:
                assert security == [{"sessionCookie": [], "csrfToken": []}]


def test_every_reference_resolves() -> None:
    document = served()
    schemas = document["components"]["schemas"]

    def refs(node: Any) -> list[str]:
        if isinstance(node, dict):
            found = [node["$ref"]] if "$ref" in node else []
            return found + [r for v in node.values() for r in refs(v)]
        if isinstance(node, list):
            return [r for v in node for r in refs(v)]
        return []

    for ref in refs(document):
        assert ref.startswith("#/components/schemas/")
        assert ref.rsplit("/", 1)[1] in schemas, ref


def test_every_operation_documents_the_error_problem_shape() -> None:
    for item in served()["paths"].values():
        for op in item.values():
            for status, response in op["responses"].items():
                if int(status) >= 400:
                    assert "application/problem+json" in response["content"]


def test_request_schemas_forbid_unknown_fields() -> None:
    schemas = served()["components"]["schemas"]
    for name in ("StartScanRequest", "TriageRequest", "MemberRoleRequest"):
        assert schemas[name]["additionalProperties"] is False


@pytest.mark.parametrize(
    ("literal", "values"),
    [
        (m.Role, {r.value for r in Role}),
        (m.Severity, {s.value for s in Severity}),
        (m.TriageState, {s.value for s in TriageState}),
    ],
)
def test_documented_enums_match_the_domain(literal: Any, values: set[str]) -> None:
    assert set(get_args(literal.__value__)) == values


def test_other_documented_literals_match_the_domain() -> None:
    schemas = served()["components"]["schemas"]
    assert set(schemas["ScanSummaryOut"]["properties"]["verdict"]["enum"]) == {
        v.value for v in Verdict
    }
    assert set(schemas["VerdictReasonOut"]["properties"]["kind"]["enum"]) == {
        k.value for k in ReasonKind
    }
    assert set(schemas["ScanLogOut"]["properties"]["level"]["enum"]) == {
        level.value for level in LogLevel
    }
    assert set(schemas["OrganizationOut"]["properties"]["status"]["enum"]) == {
        s.value for s in OrganizationStatus if s is not OrganizationStatus.UNINSTALLED
    }
