"""Endpoint-level tests for the bounded pagination on artifact listing.

``list_artifacts`` declares ``limit`` as ``ge=1, le=200`` and ``offset`` as
``ge=0, le=1000000``. The sweep that added those bounds is only worth anything
if an out-of-range request is actually rejected at the HTTP boundary, so these
tests drive the real route through FastAPI's validation rather than calling the
endpoint function with values it would never receive.

FastAPI resolves and validates parameters before invoking the endpoint, so the
422 responses below never reach the artifact manager: the only dependency is
``get_current_user``, which is overridden here to keep the app auth-free.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.authentication import get_current_user
from src.api.routes.artifacts.routes import router as artifacts_router

_LIST_URL = "/api/v1/artifacts"


class _StubUser:
    """Stand-in for TokenInfo; validation failures happen before it is used."""

    user_id = 1
    username = "tester"


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifacts_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: _StubUser()
    return TestClient(app)


@pytest.mark.parametrize(
    "query",
    [
        "offset=-1",
        "offset=1000001",
        "limit=0",
        "limit=201",
        "offset=abc",
        "limit=1.5",
    ],
)
def test_out_of_range_pagination_is_rejected(client: TestClient, query: str) -> None:
    """A bound violation must fail as a client error, not reach the manager."""
    response = client.get(f"{_LIST_URL}?{query}")
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "query",
    ["offset=0", "offset=1000000", "limit=1", "limit=200"],
)
def test_boundary_values_pass_validation(client: TestClient, query: str) -> None:
    """The bounds are inclusive; the edges must not be rejected as 422."""
    response = client.get(f"{_LIST_URL}?{query}")
    assert response.status_code != 422, response.text


def test_openapi_documents_the_pagination_bounds(client: TestClient) -> None:
    """The published schema must carry the same bounds the code enforces."""
    schema = client.get("/openapi.json").json()
    params = {
        param["name"]: param
        for param in schema["paths"][_LIST_URL]["get"]["parameters"]
    }
    assert params["offset"]["schema"]["minimum"] == 0
    assert params["offset"]["schema"]["maximum"] == 1000000
    assert params["limit"]["schema"]["minimum"] == 1
    assert params["limit"]["schema"]["maximum"] == 200
