"""The public capabilities snapshot must fail closed on access-token state.

``/capabilities`` tells clients whether the server requires an API access
token. The authoritative auth manager fails closed (it advertises "required"
when its config or token-table query fails), so a wrapper that degrades to
``False`` on the same error would advertise a weaker posture than the server
actually enforces. The degraded answer must also not be memoized, otherwise
clients stay on the wrong contract for the whole snapshot TTL.

The route module is loaded directly so the test does not depend on the rest of
the API router graph being importable at collection time.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel, ConfigDict

import src.core.auth as auth_module


class _Response(BaseModel):
    """Minimal stand-in for the two response models the route constructs."""

    model_config = ConfigDict(extra="allow")


def _load_route_module():
    module_name = "_capabilities_config_route_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    stubs = {}
    for name, attrs in {
        "src.api.schemas.config": {
            "ServerCapabilitiesResponse": _Response,
            "AvatarConfigResponse": _Response,
        },
        "src.api.schemas.common": {"ErrorResponse": object},
        "src.api.routes.auth.oauth_config": {
            "OAUTH_PROVIDERS": ("google", "github", "microsoft", "gitlab")
        },
    }.items():
        stub = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(stub, key, value)
        stubs[name] = stub

    path = Path(__file__).parents[2] / "api" / "routes" / "config.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # Keep the stand-ins scoped to this one import. Leaving them in
    # sys.modules shadows the real schemas for every module collected
    # afterwards (e.g. ``from src.api.schemas.common import SnowflakeID``),
    # and a bare ModuleType stub has no __file__ so the failure surfaces as
    # "cannot import name ... (unknown location)".
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


route_config = _load_route_module()


@pytest.fixture(autouse=True)
def _cold_snapshot():
    route_config.clear_capabilities_snapshot_cache()
    yield
    route_config.clear_capabilities_snapshot_cache()


@pytest.mark.asyncio
async def test_access_token_state_fails_closed_when_auth_check_raises(monkeypatch):
    def _boom():
        raise RuntimeError("auth backend unavailable")

    monkeypatch.setattr(auth_module, "is_api_access_token_required", _boom)

    result = await route_config.get_capabilities()

    assert result.access_token_required is True
    # A degraded answer must never poison the shared snapshot cache.
    assert route_config._snapshot_cache is None


@pytest.mark.asyncio
async def test_degraded_snapshot_is_retried_on_the_next_poll(monkeypatch):
    calls = []

    def _flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient auth failure")
        return False

    monkeypatch.setattr(auth_module, "is_api_access_token_required", _flaky)

    first = await route_config.get_capabilities()
    second = await route_config.get_capabilities()

    assert first.access_token_required is True
    assert second.access_token_required is False
    assert len(calls) == 2
