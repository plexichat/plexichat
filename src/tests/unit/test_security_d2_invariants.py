"""Security invariants that must hold across the whole API surface.

Three cross-cutting guarantees are asserted here:

* **headers** -- every response carries the canonical security header set, and
  a weakened upstream value is replaced rather than preserved;
* **body caps** -- a global request-body cap is wired in, endpoint-level caps
  reject both lying ``Content-Length`` headers and genuinely oversized reads;
* **snowflakes** -- no route declares an id path parameter as a bare ``int``,
  and hostile ids are refused before they reach a handler.

The route-tree checks are deliberately structural so a *new* route cannot
silently opt out of any of these guarantees.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.api.config import get_api_config
from src.api.middleware.security import SecurityHeadersMiddleware
from src.api.middleware.security_header_audit import audit_security_headers
from src.api.middleware.security_header_policy import (
    CANONICAL_HEADERS,
    canonical_security_headers,
)
from src.api.request_guards import read_body_capped, read_upload_capped
from src.api.routes import create_api_router
from src.api.routes.channels.crud import MAX_CHANNEL_UPDATE_BODY
from src.api.routes.messages.helpers import require_snowflake
from src.api.routes.artifacts.validation import parse_snowflake_id as artifact_parse_id
from src.api.schemas.common import SnowflakeID, SnowflakeIDParam
from src.api.websocket.boundaries import parse_snowflake
from src.core.security.hardening import SNOWFLAKE_MAX, parse_id

ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# route-tree helpers
# ---------------------------------------------------------------------------


def _iter_routes():
    def walk(router, prefix=""):
        for route in router.routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                ctx = getattr(route, "include_context", None)
                yield from walk(original, prefix + (getattr(ctx, "prefix", "") or ""))
            elif hasattr(route, "methods") and hasattr(route, "path"):
                yield prefix + route.path, route

    yield from walk(create_api_router())


def _id_path_params():
    found = []
    for path, route in _iter_routes():
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for param in dependant.path_params:
            if param.name == "id" or param.name.endswith("_id"):
                annotation = getattr(param.field_info, "annotation", None)
                found.append((path, param.name, annotation))
    return found


class TestHeadersOnEveryRoute:
    def test_canonical_policy_is_complete(self):
        declared = {name.lower(): value for name, value in canonical_security_headers()}
        assert declared == CANONICAL_HEADERS

    def test_middleware_stamps_and_repairs_a_weakened_response(self):
        async def handler(request):
            return JSONResponse(
                {"ok": True},
                headers={
                    "X-Content-Type-Options": "sniff",  # weakened upstream
                    "Referrer-Policy": "unsafe-url",  # weakened upstream
                    "X-Frame-Options": "ALLOWALL",
                },
            )

        app = SecurityHeadersMiddleware(
            Starlette(routes=[Route("/api/v1/probe", handler)])
        )
        response = TestClient(app).get("/api/v1/probe")
        raw = list(response.headers.raw)
        assert audit_security_headers(raw) == []
        values = {name.lower(): value for name, value in raw}
        assert values[b"x-content-type-options"] == b"nosniff"
        assert values[b"referrer-policy"] == b"strict-origin-when-cross-origin"
        assert values[b"x-frame-options"] == b"SAMEORIGIN"

    @pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/ready"])
    def test_real_routes_carry_the_full_header_set(self, test_client, path):
        response = test_client.get(path)
        assert audit_security_headers(list(response.headers.raw)) == []

    def test_security_headers_middleware_is_wired_into_the_app(self):
        source = (ROOT / "src/api/app.py").read_text(encoding="utf-8")
        assert "app.add_middleware(SecurityHeadersMiddleware)" in source


class TestBodyCaps:
    def test_global_body_cap_is_configured(self):
        assert get_api_config().max_request_body_size > 0

    def test_max_body_size_middleware_is_wired_into_the_app(self):
        source = (ROOT / "src/api/app.py").read_text(encoding="utf-8")
        assert "app.add_middleware(MaxBodySizeMiddleware)" in source

    def test_channel_update_cap_is_bounded(self):
        assert 0 < MAX_CHANNEL_UPDATE_BODY <= 1024 * 1024

    def test_declared_oversize_body_is_rejected(self):
        class Request:
            headers = {"content-length": str(MAX_CHANNEL_UPDATE_BODY + 1)}

            async def body(self):
                return b""

        with pytest.raises(HTTPException) as exc:
            asyncio.run(read_body_capped(Request(), MAX_CHANNEL_UPDATE_BODY))
        assert exc.value.status_code == 413

    def test_streamed_oversize_body_is_rejected(self):
        class Request:
            headers = {}

            async def body(self):
                return b"x" * (MAX_CHANNEL_UPDATE_BODY + 1)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(read_body_capped(Request(), MAX_CHANNEL_UPDATE_BODY))
        assert exc.value.status_code == 413

    @pytest.mark.parametrize("declared", ["abc", "-5"])
    def test_malformed_content_length_is_rejected(self, declared):
        class Request:
            headers = {"content-length": declared}

            async def body(self):
                return b""

        with pytest.raises(HTTPException) as exc:
            asyncio.run(read_body_capped(Request(), 100))
        assert exc.value.status_code == 400

    def test_oversize_upload_is_rejected_from_declared_size(self):
        class Upload:
            size = 1024

            async def read(self, size=-1):
                return b"x" * 10

        with pytest.raises(HTTPException) as exc:
            asyncio.run(read_upload_capped(Upload(), 100))  # pyright: ignore[reportArgumentType]
        assert exc.value.status_code == 413

    def test_oversize_upload_is_rejected_from_actual_bytes(self):
        class Upload:
            size = None

            async def read(self, size=-1):
                return b"x" * 101

        with pytest.raises(HTTPException) as exc:
            asyncio.run(read_upload_capped(Upload(), 100))  # pyright: ignore[reportArgumentType]
        assert exc.value.status_code == 413

    def test_oversized_channel_update_is_not_accepted_as_a_valid_body(
        self, test_client, auth_headers
    ):
        response = test_client.patch(
            "/api/v1/channels/1",
            headers={**auth_headers, "content-type": "application/json"},
            content=b"x" * (MAX_CHANNEL_UPDATE_BODY + 1),
        )
        assert 400 <= response.status_code < 500, response.text


class TestSnowflakeParsingOnEveryRoute:
    def test_route_discovery_is_not_vacuous(self):
        params = _id_path_params()
        assert len(params) > 200, f"only found {len(params)} id path params"

    def test_no_route_declares_an_id_param_as_a_bare_integer(self):
        offenders = [
            f"{path}:{name}={annotation!r}"
            for path, name, annotation in _id_path_params()
            if annotation in (int, float, bool)
        ]
        assert not offenders, "bare numeric id params:\n  " + "\n  ".join(offenders)

    def test_some_routes_use_the_typed_snowflake_params(self):
        typed = [
            (path, name)
            for path, name, annotation in _id_path_params()
            if annotation in (SnowflakeID, SnowflakeIDParam)
        ]
        assert len(typed) > 20, f"only {len(typed)} typed snowflake params"

    @pytest.mark.parametrize(
        "value", [-1, 2**63, 2**70, "abc", "1; DROP TABLE users", True, None, [], {}]
    )
    @pytest.mark.parametrize("model", [SnowflakeID, SnowflakeIDParam])
    def test_snowflake_types_reject_hostile_values(self, model, value):
        with pytest.raises(Exception):
            TypeAdapter(model).validate_python(value)

    @pytest.mark.parametrize(
        "value", [-1, 0, 2**63, 2**70, "abc", "1; DROP TABLE users"]
    )
    @pytest.mark.parametrize(
        "guard", [parse_id, require_snowflake, parse_snowflake, artifact_parse_id]
    )
    def test_shared_snowflake_guards_reject_hostile_values(self, guard, value):
        with pytest.raises((HTTPException, ValueError)):
            guard(str(value), "id")

    def test_snowflake_ceiling_is_the_signed_64_bit_max(self):
        assert SNOWFLAKE_MAX == 2**63 - 1

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/channels/not-an-id",
            "/api/v1/channels/-1",
            "/api/v1/channels/99999999999999999999",
            "/api/v1/servers/not-an-id/channels",
            "/api/v1/artifacts/abc",
        ],
    )
    def test_routes_reject_hostile_snowflakes(self, test_client, auth_headers, path):
        response = test_client.get(path, headers=auth_headers)
        assert 400 <= response.status_code < 500, response.text
