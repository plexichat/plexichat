"""Auth id params must accept the decimal string form of a snowflake.

The auth session/device/passkey routes type their id params as ``SnowflakeID``
(int|string), so a browser client can send the exact decimal string instead of
rounding it through a JS ``Number``. These tests drive the real sub-routers and
assert the string and int forms are accepted and behave identically — a route
still typed as a pure ``int`` would reject the string form with ``422``.

No auth patching is needed: FastAPI validates the param before the handler
runs, so the unauthenticated case is a ``401`` from ``get_current_user`` and a
numeric-only regression would show up as a ``422`` mismatch.
"""

import importlib
import re
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.events.snowflakes import is_id_field

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision

# (label, module, method, path-template under /api/v1/auth, json body)
CASES = [
    (
        "sessions_revoke",
        "src.api.routes.auth.sessions",
        "DELETE",
        "/sessions/{session_id}",
        None,
    ),
    (
        "devices_rename",
        "src.api.routes.auth.devices",
        "PATCH",
        "/devices/{device_id}",
        {"name": "renamed"},
    ),
    (
        "devices_revoke",
        "src.api.routes.auth.devices",
        "DELETE",
        "/devices/{device_id}",
        None,
    ),
    (
        "passkeys_revoke",
        "src.api.routes.auth.passkeys",
        "DELETE",
        "/passkeys/{passkey_id}",
        None,
    ),
    (
        "passkeys_rename",
        "src.api.routes.auth.passkeys",
        "PATCH",
        "/passkeys/{passkey_id}",
        {"name": "renamed"},
    ),
]

_IDS = [label for label, *_ in CASES]


def _fill(path: str, value) -> str:
    return re.sub(r"\{[a-z_]+\}", str(value), path)


def _app(module_path: str) -> FastAPI:
    module = importlib.import_module(module_path)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/auth")
    return app


def _client(module_path: str) -> TestClient:
    return TestClient(_app(module_path), raise_server_exceptions=False)


def _described_types(prop: dict) -> set:
    types: set = set()
    if "type" in prop:
        types.add(prop["type"])
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in prop.get(key, []) or []:
            types |= _described_types(branch)
    return types


@pytest.mark.parametrize("label,module_path,method,path,body", CASES, ids=_IDS)
def test_auth_param_accepts_decimal_string(
    label: str, module_path: str, method: str, path: str, body: Optional[dict]
) -> None:
    client = _client(module_path)
    url_string = "/api/v1/auth" + _fill(path, str(BIG))
    url_int = "/api/v1/auth" + _fill(path, BIG)

    string_resp = client.request(method, url_string, json=body)
    int_resp = client.request(method, url_int, json=body)

    # A string id must never be a validation error...
    assert string_resp.status_code != 422, string_resp.text
    # ...and must behave exactly like the int form.
    assert int_resp.status_code != 422, int_resp.text
    assert string_resp.status_code == int_resp.status_code, (
        f"{label}: string={string_resp.status_code} int={int_resp.status_code}"
    )


@pytest.mark.parametrize("label,module_path,method,path,body", CASES, ids=_IDS)
def test_auth_id_param_declares_string(
    label: str, module_path: str, method: str, path: str, body: Optional[dict]
) -> None:
    """The OpenAPI param must advertise a ``string`` type."""
    schema = _app(module_path).openapi()
    path_key = "/api/v1/auth" + path
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
