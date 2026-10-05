"""Non-admin id params must accept the decimal string form of a snowflake.

URL path segments and query values are always strings on the wire, so a route
whose id param rejects the exact decimal string would force a browser client to
round a 64-bit id through a JS ``Number``. The admin routes have
``test_admin_param_string_ids.py``; this mirrors it for the non-admin surface.

Each module's id params are discovered from its own OpenAPI schema (so the
list cannot silently rot when a route moves), then driven with a value past
``2**53``. FastAPI validates params before the handler runs, so an accepted id
falls through to the handler's own ``401``/``403``/``404``/``500`` — only a
``422`` means the param refused the string.
"""

import importlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.authentication import (
    get_current_user,
    get_optional_user,
)
from src.core.auth.models import AccountType, TokenInfo
from src.core.events.snowflakes import is_id_field

BIG = 9007199254740993  # 2**53 + 1

# Non-admin routers that declare a snowflake id param. The id params are
# discovered from each module's OpenAPI, not hand-listed here.
NON_ADMIN_ROUTE_MODULES = [
    "src.api.routes.artifacts",
    "src.api.routes.avatars",
    "src.api.routes.bots",
    "src.api.routes.emojis",
    "src.api.routes.features.profiles",
    "src.api.routes.features.reports",
    "src.api.routes.join",
    "src.api.routes.messages.messages_crud",
    "src.api.routes.polls",
    "src.api.routes.presence",
    "src.api.routes.relationships",
    "src.api.routes.suite.stash",
]

_METHODS = {"get", "post", "put", "patch", "delete"}


def _stub_user() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=BIG,
        user_id=BIG,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


def _app(module_path: str) -> FastAPI:
    module = importlib.import_module(module_path)
    app = FastAPI()
    # A non-empty prefix is required: some routers declare a path operation on
    # an empty path (e.g. relationships' ``create_relationship``), which
    # FastAPI rejects when the include prefix is also empty.
    app.include_router(module.router, prefix="/api/v1")
    # Stub auth so a dependency cannot short-circuit with 401 before FastAPI
    # validates the id param (which would mask a 422 and make this vacuous).
    user = _stub_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_optional_user] = lambda: user
    return app


def _described_types(prop: dict) -> set:
    types: set = set()
    if "type" in prop:
        types.add(prop["type"])
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in prop.get(key, []) or []:
            types |= _described_types(branch)
    return types


def _value_for(param: dict, id_value: str) -> str:
    """A plausible value for a non-id param so it cannot itself 422."""
    if is_id_field(param.get("name", "")):
        return id_value
    types = _described_types(param.get("schema", {}))
    if "integer" in types and "string" not in types:
        return "1"
    return "x"


def _request_url(path: str, operation: dict, id_value: str) -> str:
    params = {p["name"]: p for p in operation.get("parameters", []) or []}

    def repl(match: re.Match) -> str:
        name = match.group(1)
        return _value_for(params.get(name, {"name": name, "schema": {}}), id_value)

    url = re.sub(r"\{([^}]+)\}", repl, path)
    for name, param in params.items():
        if param.get("in") == "query" and is_id_field(name):
            sep = "&" if "?" in url else "?"
            url += f"{sep}{name}={id_value}"
    return url


def _id_param_rejected(response, name: str) -> bool:
    """True only when a 422 error is actually about the id param.

    A missing request body or another required query param also produces a
    422, so a bare status check would misattribute it to the id param.
    """
    if response.status_code != 422:
        return False
    try:
        detail = response.json().get("detail")
    except Exception:
        return False
    if not isinstance(detail, list):
        return False
    return any(name in (err.get("loc") or []) for err in detail)


def _id_params(schema: dict):
    """Yield ``(method, path, operation, param)`` for every id param."""
    for path, item in schema.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method not in _METHODS or not isinstance(operation, dict):
                continue
            for param in operation.get("parameters", []) or []:
                if is_id_field(param.get("name", "")):
                    yield method, path, operation, param


@pytest.mark.parametrize("module_path", NON_ADMIN_ROUTE_MODULES)
def test_non_admin_id_params_accept_decimal_string(module_path):
    app = _app(module_path)
    client = TestClient(app, raise_server_exceptions=False)
    schema = app.openapi()

    offenders = []
    for method, path, operation, param in _id_params(schema):
        name = param["name"]
        url = _request_url(path, operation, str(BIG))
        response = client.request(method.upper(), url)
        if _id_param_rejected(response, name):
            offenders.append(
                f"{method.upper()} {path} {name} -> 422 {response.text[:200]}"
            )

    assert not offenders, (
        "Non-admin id params must accept the exact decimal string; these "
        "rejected it:\n" + "\n".join(offenders)
    )


def test_non_admin_route_modules_expose_id_params():
    """Guard against the sweep going vacuous if a module stops declaring ids."""
    total = 0
    for module_path in NON_ADMIN_ROUTE_MODULES:
        schema = _app(module_path).openapi()
        total += sum(1 for _ in _id_params(schema))
    assert total >= 15, f"only {total} non-admin id params discovered"
