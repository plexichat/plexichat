"""Regression: the profile-visibility cache key must be invalidatable.

``GET /users/{user_id}/profile`` receives ``user_id`` as a ``SnowflakeIDParam``
(an ``int`` subclass).  ``@cached`` builds its key from the runtime type name,
so reading visibility with that object stores the value under
``profile_visibility:SnowflakeIDParam:<id>``.  The settings write path, however,
invalidates ``profile_visibility:int:<id>``.  The keys never matched, so a
permissive visibility value stayed warm for the whole TTL after the owner
tightened it -- continuing to serve private profile fields (location, timezone,
pronouns, bio, custom status) to viewers who should have been restricted.

The fix normalizes the id to a plain ``int`` at the cache boundary so the read
key and the invalidation key are identical.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.api as api
from src.api.routes.features import profiles as profiles_routes
from src.api.schemas.common import SnowflakeIDParam
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache.operations import invalidate_pattern

pytestmark = [pytest.mark.unit]

OWNER = 91001
VIEWER = 82001


def _token(uid: int) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user-{uid}",
        account_type=AccountType.USER,
    )


def _profile(uid: int) -> dict:
    return {
        "id": 70001,
        "user_id": uid,
        "bio": "private bio",
        "banner_url": None,
        "social_links": [],
        "custom_status_text": "private status",
        "custom_status_emoji": None,
        "custom_status_expires_at": None,
        "pronouns": "they/them",
        "location": "Secret Location",
        "timezone": "Secret/Zone",
        "created_at": 1,
        "updated_at": 1,
    }


class _FakeSettings:
    def __init__(self):
        self.values: dict = {}

    def get_setting(self, user_id, key):
        return self.values.get((int(user_id), key))


class _FakeRelationships:
    def get_friend_ids(self, owner_id):
        return set()

    def get_mutual_servers(self, a, b):
        return False


class _FakeDB:
    def __init__(self, profile: dict):
        self.profile = profile

    def fetch_one(self, sql, params=()):
        if "FROM user_profiles" in sql:
            return dict(self.profile)
        return None

    def fetch_all(self, sql, params=()):
        return []

    def execute(self, sql, params=()):
        return None


@pytest.fixture(autouse=True)
def _cache_env():
    with (
        patch("src.core.database.cache.decorators.get_client", return_value=None),
        patch("src.core.database.cache.operations.get_client", return_value=None),
    ):
        invalidate_pattern("profile_visibility:*")
        invalidate_pattern("user_profile_read_api:*")
        yield
        invalidate_pattern("profile_visibility:*")
        invalidate_pattern("user_profile_read_api:*")


@pytest.fixture
def env():
    settings = _FakeSettings()
    db = _FakeDB(_profile(OWNER))
    relationships = _FakeRelationships()
    with (
        patch.object(api, "get_db", return_value=db),
        patch.object(api, "get_settings", return_value=settings),
        patch.object(api, "get_relationships", return_value=relationships),
        patch.object(api, "get_servers", return_value=None),
    ):
        yield SimpleNamespace(settings=settings, db=db, relationships=relationships)


@pytest.mark.asyncio
async def test_visibility_tightening_is_reflected_after_settings_invalidation(env):
    """A public -> private change must take effect even with a warm cache."""
    viewer = _token(VIEWER)
    owner_param = SnowflakeIDParam(OWNER)  # exactly what FastAPI passes to the route

    # A permissive value is cached by the first viewer read.
    env.settings.values[(OWNER, "profile_visibility")] = "everyone"
    first = await profiles_routes.get_user_profile(owner_param, current_user=viewer)
    assert first["profile"]["location"] == "Secret Location"
    assert first["profile"]["bio"] == "private bio"
    assert first["profile"]["custom_status_text"] == "private status"

    # The owner tightens visibility; the settings write path invalidates by int.
    env.settings.values[(OWNER, "profile_visibility")] = "private"
    profiles_routes.invalidate_profile_visibility_cache(OWNER)

    # A fresh request must now be projected to the minimal private identity.
    after = await profiles_routes.get_user_profile(owner_param, current_user=viewer)
    assert after["profile"]["user_id"] == str(OWNER)
    assert "location" not in after["profile"]
    assert "timezone" not in after["profile"]
    assert "pronouns" not in after["profile"]
    assert "bio" not in after["profile"]
    assert "custom_status_text" not in after["profile"]


@pytest.mark.asyncio
async def test_visibility_cache_key_agrees_with_invalidation_key(env):
    """The read path and the settings write path use the same cache key."""
    from src.core.database.cache.serialization import generate_cache_key

    owner_param = SnowflakeIDParam(OWNER)
    read_key = generate_cache_key("profile_visibility", int(owner_param))
    invalidation_key = f"profile_visibility:int:{int(owner_param)}"
    assert read_key == invalidation_key

    # The resolved visibility is served from that exact key: a warm read comes
    # from the cache, and invalidating it forces a fresh settings read.
    env.settings.values[(OWNER, "profile_visibility")] = "friends"
    viewer = _token(VIEWER)
    limited = await profiles_routes.get_user_profile(owner_param, current_user=viewer)
    assert "location" not in limited["profile"]

    env.settings.values[(OWNER, "profile_visibility")] = "everyone"
    profiles_routes.invalidate_profile_visibility_cache(OWNER)
    full = await profiles_routes.get_user_profile(owner_param, current_user=viewer)
    assert full["profile"]["location"] == "Secret Location"
