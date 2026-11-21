"""The public capabilities snapshot must fail closed on access-token state.

``/capabilities`` tells clients whether the server requires an API access
token. The authoritative auth manager fails closed (it advertises "required"
when its config or token-table query fails), so a wrapper that degrades to
``False`` on the same error would advertise a weaker posture than the server
actually enforces. The degraded answer must also not be memoized, otherwise
clients stay on the wrong contract for the whole snapshot TTL.
"""

import importlib

import pytest

# ``src.api.routes`` shadows its ``config`` submodule attribute with
# ``utils.config`` at package init, so resolve the module by full name.
route_config = importlib.import_module("src.api.routes.config")
import src.core.auth as auth_module


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
