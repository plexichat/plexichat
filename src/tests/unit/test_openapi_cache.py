"""Regression tests for the on-disk OpenAPI schema cache.

FastAPI (>= 0.140) rebuilds the schema whenever the router's route-version
counter moves (``app._openapi_routes_version``). Loading a cached document into
``app.openapi_schema`` without also recording that counter makes the very first
``app.openapi()`` call regenerate the whole schema, so the disk cache never
actually saved any work. These tests pin that the loaded document is reused
without regeneration and that schema correctness is preserved.
"""

import pytest
from fastapi import FastAPI

from src.api import openapi_cache


def _make_app():
    app = FastAPI(version="cache-test-1")

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PLEXICHAT_CACHE_DIR", str(tmp_path))
    return tmp_path


def test_loaded_schema_is_reused_without_regeneration(cache_dir, monkeypatch):
    first = _make_app()
    generated = first.openapi()
    assert first.openapi_schema is not None
    openapi_cache.save_cached_openapi(first)

    second = _make_app()
    assert second.openapi_schema is None
    assert openapi_cache.load_cached_openapi(second) is True

    import fastapi.applications as fa

    def _boom(*args, **kwargs):
        raise AssertionError("OpenAPI schema was regenerated despite a cache hit")

    monkeypatch.setattr(fa, "get_openapi", _boom)

    result = second.openapi()
    # No regeneration: the loaded object is returned verbatim and still valid.
    assert result is second.openapi_schema
    assert result == generated
    assert "paths" in result
    assert "/ping" in result["paths"]


def test_load_records_current_routes_version(cache_dir):
    first = _make_app()
    first.openapi()
    openapi_cache.save_cached_openapi(first)

    fresh = _make_app()
    assert getattr(fresh, "_openapi_routes_version", None) is None
    assert openapi_cache.load_cached_openapi(fresh) is True
    assert fresh._openapi_routes_version == fresh.router._get_routes_version()


def test_cache_miss_when_route_set_changes(cache_dir):
    first = _make_app()
    first.openapi()
    openapi_cache.save_cached_openapi(first)

    # A different route set changes the cache key -> genuine miss.
    other = FastAPI(version="cache-test-1")

    @other.get("/other")
    def other_route():
        return {}

    assert openapi_cache.load_cached_openapi(other) is False
    assert other.openapi_schema is None
