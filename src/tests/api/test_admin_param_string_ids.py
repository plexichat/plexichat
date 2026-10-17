"""Admin id params must accept the decimal string form of a snowflake.

The admin routes were switched from ``int`` to ``SnowflakeIDParam`` so the
generated OpenAPI advertises ``integer|string`` (a JS client can send the
exact decimal string instead of a rounded ``Number``). These tests drive the
real routers and assert that the string and int forms are accepted and behave
identically — a route that still typed the param as a pure ``int`` would reject
nothing extra here, but the paired OpenAPI assertion in
``test_openapi_snowflake_ids`` covers the declaration, and this catches a
future regression where a param is re-typed in a way that breaks the string
form (e.g. a stray ``:int`` path converter).

No auth patching is needed: FastAPI validates params before the handler runs,
so a rejected param is ``422`` and an accepted one falls through to the
handler's own ``401``/``403``/``404`` — which is all we compare.
"""

import importlib
import re
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.events.snowflakes import is_id_field

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision

# (label, module, method, path-template under /api/v1/admin)
CASES = [
    (
        "approvals_get",
        "src.api.routes.admin.approvals",
        "GET",
        "/approvals/{approval_id}",
    ),
    (
        "approvals_comments",
        "src.api.routes.admin.approvals",
        "GET",
        "/approvals/{approval_id}/comments",
    ),
    (
        "approvals_delete",
        "src.api.routes.admin.approvals",
        "DELETE",
        "/approvals/{approval_id}",
    ),
    ("roles_get", "src.api.routes.admin.roles", "GET", "/roles/{role_id}"),
    (
        "roles_admin_roles",
        "src.api.routes.admin.roles",
        "GET",
        "/admins/{admin_id}/roles",
    ),
    ("roles_delete", "src.api.routes.admin.roles", "DELETE", "/roles/{role_id}"),
    (
        "roles_revoke",
        "src.api.routes.admin.roles",
        "DELETE",
        "/roles/{admin_id}/{role_id}",
    ),
    (
        "security_token_get",
        "src.api.routes.admin.security",
        "GET",
        "/security/access-tokens/{token_id}",
    ),
    (
        "security_scope_delete",
        "src.api.routes.admin.security",
        "DELETE",
        "/security/access-tokens/{token_id}/scopes/{scope_id}",
    ),
    (
        "security_banned_username_delete",
        "src.api.routes.admin.security",
        "DELETE",
        "/security/banned-usernames/{pattern_id}",
    ),
    ("tickets_get", "src.api.routes.admin.tickets", "GET", "/tickets/{ticket_id}"),
    (
        "tickets_notes",
        "src.api.routes.admin.tickets",
        "GET",
        "/tickets/{ticket_id}/notes",
    ),
    (
        "automod_rule_delete",
        "src.api.routes.admin.moderation",
        "DELETE",
        "/automod/rules/{rule_id}",
    ),
    (
        "plexijoin_connection",
        "src.api.routes.admin.plexijoin",
        "GET",
        "/plexijoin/connections/{connection_id}",
    ),
    (
        "plexijoin_test",
        "src.api.routes.admin.plexijoin",
        "POST",
        "/plexijoin/connections/{connection_id}/test",
    ),
    (
        "plexijoin_request_approve",
        "src.api.routes.admin.plexijoin",
        "POST",
        "/plexijoin/requests/{request_id}/approve",
    ),
    (
        "data_export_get",
        "src.api.routes.admin.data_export",
        "GET",
        "/data-export/{request_id}",
    ),
    (
        "audit_logs",
        "src.api.routes.admin.audit",
        "GET",
        "/audit/logs?admin_id={admin_id}",
    ),
    (
        "audit_export",
        "src.api.routes.admin.audit",
        "GET",
        "/audit/logs/export?admin_id={admin_id}",
    ),
    (
        "artifacts_feature",
        "src.api.routes.admin.artifacts",
        "GET",
        "/artifacts/features/{feature}?server_id={server_id}",
    ),
    (
        "reindex_conversation",
        "src.api.routes.admin.reindex",
        "POST",
        "/search/reindex/conversation/{conversation_id}",
    ),
]

_IDS = [label for label, *_ in CASES]


def _fill(path: str, value) -> str:
    return re.sub(r"\{[a-z_]+\}", str(value), path)


def _app(module_path: str) -> FastAPI:
    module = importlib.import_module(module_path)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/admin")
    return app


def _client(module_path: str) -> TestClient:
    # ``check_host_restriction`` consults the licensing singleton, which a bare
    # router test does not configure; a 500 from the handler still proves the
    # param passed validation, so surface it as a response instead of raising.
    return TestClient(_app(module_path), raise_server_exceptions=False)


def _described_types(prop: dict) -> set:
    types: set = set()
    if "type" in prop:
        types.add(prop["type"])
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in prop.get(key, []) or []:
            types |= _described_types(branch)
    return types


@pytest.mark.parametrize("label,module_path,method,path", CASES, ids=_IDS)
def test_admin_param_accepts_decimal_string(label, module_path, method, path):
    client = _client(module_path)
    url_string = "/api/v1/admin" + _fill(path, str(BIG))
    url_int = "/api/v1/admin" + _fill(path, BIG)

    string_resp = client.request(method, url_string)
    int_resp = client.request(method, url_int)

    # A string id must never be a validation error...
    assert string_resp.status_code != 422, string_resp.text
    # ...and must behave exactly like the int form.
    assert int_resp.status_code != 422, int_resp.text
    assert string_resp.status_code == int_resp.status_code, (
        f"{label}: string={string_resp.status_code} int={int_resp.status_code}"
    )


@pytest.mark.parametrize("label,module_path,method,path", CASES, ids=_IDS)
def test_admin_id_param_declares_string(label, module_path, method, path):
    """The OpenAPI param must advertise ``integer|string``.

    This is what actually distinguishes ``SnowflakeIDParam`` from a plain
    ``int``: a path/query value is always a string on the wire, so only the
    *declaration* stops a generated JS client from rounding the id.
    """
    schema = _app(module_path).openapi()
    path_key = "/api/v1/admin" + path.split("?")[0]
    operation = schema["paths"][path_key][method.lower()]

    id_params = [
        p for p in operation.get("parameters", []) if is_id_field(p.get("name", ""))
    ]
    assert id_params, f"{label}: no id param found at {path_key}"
    for param in id_params:
        types: Any = _described_types(param.get("schema", {}))
        assert "string" in types, (
            f"{label}: param {param['name']} declares {sorted(types)} (missing string)"
        )
