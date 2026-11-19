"""Regression: the PlexiSum viewport ``filters`` query param is bounded.

``filters`` arrives in the query string (outside the request-body cap) and is
fed straight to ``json.loads`` at the route boundary.  Unlike every other
string query parameter in the API it had no ``max_length``, so a caller could
make us allocate and parse a multi-megabyte JSON value before the manager's
``filters[:50]`` slice bounded the actual work.  This suite pins both the
FastAPI declaration and the in-handler defence-in-depth check.
"""

import asyncio
import types

import pytest
from fastapi.routing import APIRoute

from src.api.routes.suite.sum import operations
from src.api.routes.suite.sum.operations import (
    MAX_VIEWPORT_FILTERS_JSON_LENGTH,
    read_viewport,
)


def _route():
    for route in operations.router.routes:
        if isinstance(route, APIRoute) and route.name == "read_viewport":
            return route
    raise AssertionError("read_viewport route not registered")


def test_filters_query_param_declares_max_length():
    """The OpenAPI/validation boundary must carry a finite ceiling."""
    route = _route()
    param = next((p for p in route.dependant.query_params if p.name == "filters"), None)
    assert param is not None
    # FastAPI/Pydantic v2 stores the constraint in field_info.metadata as a
    # MaxLen marker (older versions expose ``max_length`` directly).
    max_length = getattr(param.field_info, "max_length", None)
    if max_length is None:
        for marker in getattr(param.field_info, "metadata", []) or []:
            candidate = getattr(marker, "max_length", None)
            if candidate is not None:
                max_length = candidate
                break
    assert max_length == MAX_VIEWPORT_FILTERS_JSON_LENGTH


def test_oversized_filters_rejected_before_manager_is_called(monkeypatch):
    """A huge blob must not reach json.loads or the manager."""
    called = {"manager": False}

    class FakeManager:
        def read_viewport(self, *args, **kwargs):
            called["manager"] = True
            raise AssertionError("manager must not be reached for oversized filters")

    monkeypatch.setattr(operations, "_manager", lambda: FakeManager())

    oversized = "[" + ("{}" * (MAX_VIEWPORT_FILTERS_JSON_LENGTH // 2 + 2)) + "]"
    assert len(oversized) > MAX_VIEWPORT_FILTERS_JSON_LENGTH

    with pytest.raises(Exception) as exc:
        asyncio.run(
            read_viewport(
                workbook_id="1",
                sheet_id="2",
                row_start=0,
                row_end=10,
                column_start=0,
                column_end=10,
                filters=oversized,
                current_user=types.SimpleNamespace(user_id=42),
            )
        )

    # The handler raises HTTPException(400); either the status code or the
    # detail text identifies the rejection, and crucially no parser/manager
    # work happened.
    assert called["manager"] is False
    detail = str(getattr(exc.value, "detail", exc.value)).lower()
    assert "too large" in detail


def test_normal_filters_still_parse_and_reach_manager(monkeypatch):
    """Ordinary, bounded filter lists must keep working."""
    captured = {}

    class FakeManager:
        def read_viewport(self, user_id, workbook_id, sheet_id, *rest):
            captured["filters"] = rest[-1]
            return {"ok": True}

    monkeypatch.setattr(operations, "_manager", lambda: FakeManager())
    filters = '[{"column": 0, "operator": "eq", "value": 5}]'

    # The handler validates the manager response through SumViewportResponse;
    # stub that so this test isolates the filters-boundary behaviour.
    monkeypatch.setattr(
        operations,
        "SumViewportResponse",
        types.SimpleNamespace(model_validate=lambda result: result),
    )

    result = asyncio.run(
        read_viewport(
            workbook_id="1",
            sheet_id="2",
            row_start=0,
            row_end=10,
            column_start=0,
            column_end=10,
            filters=filters,
            current_user=types.SimpleNamespace(user_id=42),
        )
    )
    assert result == {"ok": True}
    assert captured["filters"] == [{"column": 0, "operator": "eq", "value": 5}]
