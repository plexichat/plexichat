"""The admin tier-listing endpoint reuses a versioned tier-config snapshot.

``GET /api/v1/admin/tiers`` used to call ``get_available_tiers`` plus one
``get_tier_limits`` per tier plus ``get_default_tier`` on every request,
re-reading config and re-validating/rebuilding every ``TierLimits`` each time.
The tier table is now built once and cached, keyed on a config version token,
until ``user_features`` actually changes.

These tests pin three things: the response is byte-for-byte the same as the
freshly computed one, the heavy helpers run only on a cache miss, and none of
the auth/authorization gates were traded away for the cache.
"""

import asyncio
import copy
import json

import pytest
from fastapi import HTTPException

import src.api as api
import utils.config as config
from src.api.routes.features.tier_features import get_available_tiers as tiers_handler
from src.api.schemas.features import TierLimitsResponse, TiersResponse
from src.core import features
from src.core.admin.request_roles import clear_session_roles

_ADMIN_ID = 951


class _Admin:
    permissions = {"administrator": True}
    user_id = _ADMIN_ID


class _NonAdmin:
    permissions = {}
    user_id = _ADMIN_ID + 1


class _ScopeDB:
    """Minimal DB double granting exactly the admin config-read scope."""

    def fetch_all(self, sql, params=()):
        if "admin_roles" in sql and "admin_role_assignments" in sql:
            return [
                {
                    "id": 1,
                    "name": "config-reader",
                    "description": "",
                    "permissions": json.dumps({"config.read": True}),
                    "created_at": 0,
                    "created_by": 0,
                    "is_system": 0,
                }
            ]
        return []

    def fetch_one(self, sql, params=()):
        return None


def _call_handler(user):
    return asyncio.run(tiers_handler(current_user=user))


@pytest.fixture(autouse=True)
def _fresh_tier_cache(monkeypatch):
    """Each test starts from an empty tier table and a scoped admin session."""
    features.invalidate_tier_configuration_cache()
    monkeypatch.setattr(api, "get_db", lambda: _ScopeDB())
    clear_session_roles()
    yield
    features.invalidate_tier_configuration_cache()
    clear_session_roles()


def test_response_matches_fresh_computation(monkeypatch):
    """Caching must not change the response shape or content."""
    monkeypatch.setattr(features, "is_setup", lambda: True)

    response = _call_handler(_Admin())

    # Build the expected value with the authoritative, uncached helpers.
    tiers = features.get_available_tiers()
    expected = TiersResponse(
        tiers={
            tier: TierLimitsResponse(**features.get_tier_limits(tier).to_dict())
            for tier in tiers
        },
        default=features.get_default_tier(),
    )
    assert response == expected
    assert set(response.tiers) == set(tiers)
    assert all(
        isinstance(limits, TierLimitsResponse) for limits in response.tiers.values()
    )


def test_tier_limits_recomputed_once_across_many_requests(monkeypatch):
    """The per-tier validation/rebuild runs once, not once per request."""
    monkeypatch.setattr(features, "is_setup", lambda: True)

    real_get_tier_limits = features.get_tier_limits
    calls = []

    def counting(tier):
        calls.append(tier)
        return real_get_tier_limits(tier)

    monkeypatch.setattr(features, "get_tier_limits", counting)

    snapshots = [_call_handler(_Admin()) for _ in range(25)]

    # One build per tier in the whole run, and every response is identical.
    assert len(calls) == len(snapshots[0].tiers)
    assert all(s == snapshots[0] for s in snapshots)
    assert features._tier_configuration_snapshot.stats()["rebuilds"] >= 1


def test_config_change_invalidates_tier_table(monkeypatch):
    """An edit to the tier section is visible on the very next request."""
    monkeypatch.setattr(features, "is_setup", lambda: True)

    before = features.get_tier_configuration()
    loader = config._config_instance
    # The test config may not define user_features at all; install a minimal
    # tier section and then change it so the version token is guaranteed to
    # move. monkeypatch restores the original config afterwards.
    mutated = copy.deepcopy(loader.config.get("user_features", {}) or {})
    mutated.setdefault("rate_limit_tiers", {})
    standard = copy.deepcopy(mutated["rate_limit_tiers"].get("standard", {}) or {})
    standard["multiplier"] = 7.5
    mutated["rate_limit_tiers"]["standard"] = standard
    monkeypatch.setitem(loader.config, "user_features", mutated)
    loader._invalidate_cache()

    after = features.get_tier_configuration()

    assert after is not before
    assert after.limits["standard"].multiplier == 7.5
    # The cached snapshot was not mutated in place.
    assert before.limits["standard"].multiplier != 7.5
    # The handler observes the new value without a manual cache clear.
    assert _call_handler(_Admin()).tiers["standard"].multiplier == 7.5


def test_non_admin_is_still_rejected(monkeypatch):
    """Caching the tier table must not bypass the admin permission gate."""
    monkeypatch.setattr(features, "is_setup", lambda: True)

    with pytest.raises(HTTPException) as exc_info:
        _call_handler(_NonAdmin())

    assert exc_info.value.status_code == 403
