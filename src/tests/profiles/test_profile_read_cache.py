"""Bounded, per-credential caching for the profile metadata read.

``GET /users/{user_id}/profile`` used to resolve a ``user_profiles`` row (plus
social-link JSON and status fields) on every view. These tests pin the contract
of the new bounded cache:

* a warm read is byte-for-byte identical to a cold one but performs no profile
  query (measured query reduction);
* the cache key is partitioned per credential, so two viewers -- and a bot vs.
  its owner -- never share an entry;
* the visibility/authz decision is recomputed on *every* request before the
  cache lookup, so a relationship change is reflected without a re-fetch and
  no restricted view is ever cached;
* every profile mutation drops the cached entries, so a cached profile cannot
  survive a write.

The real route, key builder and cache decorator stay on the path; only the DB
module is replaced by a counting fake.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.api as api
from src.api.routes.features import profiles as profiles_routes
from src.api.routes.features.profiles import invalidate_profile_visibility_cache
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache.operations import invalidate_pattern
from src.core.profiles.manager import (
    PROFILE_READ_CACHE_PREFIX,
    ProfileManager,
    invalidate_profile_read_cache,
)

pytestmark = [pytest.mark.unit]

PROFILE_UID = 9001
VIEWER_UID = 8001
VIEWER2_UID = 8002
BOT_ACCOUNT_ID = 55555


def _token(uid: int, token_type: str = "user", account_id: int | None = None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=account_id if account_id is not None else uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user-{uid}",
        account_type=AccountType.USER,
    )


def _base_profile(uid: int = PROFILE_UID) -> dict:
    return {
        "id": 7001,
        "user_id": uid,
        "bio": "original bio",
        "banner_url": None,
        "social_links": [],
        "custom_status_text": None,
        "custom_status_emoji": None,
        "custom_status_expires_at": None,
        "pronouns": "they/them",
        "location": "Mars",
        "timezone": "UTC",
        "created_at": 1000,
        "updated_at": 1000,
    }


class _CountingProfilesDB:
    """In-memory stand-in that counts profile metadata resolution queries."""

    def __init__(self, profile: dict):
        self.profile = dict(profile)
        self.profile_fetch_calls: list[tuple] = []
        self.executed: list[tuple] = []

    def fetch_one(self, sql, params=()):
        if "FROM user_profiles" in sql:
            self.profile_fetch_calls.append(tuple(params))
            return dict(self.profile)
        return None

    def fetch_all(self, sql, params=()):
        return []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return None


class _FakeSettings:
    def __init__(self):
        self.values: dict = {}

    def get_setting(self, user_id, key):
        return self.values.get((int(user_id), key))


class _FakeRelationships:
    def __init__(self):
        # owner id -> set of that owner's friend ids
        self.friends: dict[int, set[int]] = {}

    def get_friend_ids(self, owner_id):
        return set(self.friends.get(owner_id, set()))

    def get_mutual_servers(self, a, b):
        return False


@pytest.fixture(autouse=True)
def _cache_env():
    """Force the process-local cache tier and clear both cache namespaces."""
    with (
        patch("src.core.database.cache.decorators.get_client", return_value=None),
        patch("src.core.database.cache.operations.get_client", return_value=None),
    ):
        invalidate_pattern(f"{PROFILE_READ_CACHE_PREFIX}:*")
        invalidate_pattern("profile_visibility:*")
        yield
        invalidate_pattern(f"{PROFILE_READ_CACHE_PREFIX}:*")
        invalidate_pattern("profile_visibility:*")


@pytest.fixture
def env():
    db = _CountingProfilesDB(_base_profile())
    settings = _FakeSettings()
    relationships = _FakeRelationships()
    with (
        patch.object(api, "get_db", return_value=db),
        patch.object(api, "get_settings", return_value=settings),
        patch.object(api, "get_relationships", return_value=relationships),
        patch.object(api, "get_servers", return_value=None),
    ):
        yield SimpleNamespace(db=db, settings=settings, relationships=relationships)


async def _read(uid: int, token):
    return await profiles_routes.get_user_profile(uid, current_user=token)


@pytest.mark.asyncio
async def test_warm_read_is_identical_and_reduces_queries(env):
    token = _token(VIEWER_UID)
    first = await _read(PROFILE_UID, token)
    second = await _read(PROFILE_UID, token)

    assert first == second
    # One profile resolution across two reads: the second was a cache hit.
    assert len(env.db.profile_fetch_calls) == 1
    assert env.db.profile_fetch_calls == [(PROFILE_UID,)]


@pytest.mark.asyncio
async def test_per_viewer_keys_do_not_leak(env):
    alice, bob = _token(VIEWER_UID), _token(VIEWER2_UID)
    await _read(PROFILE_UID, alice)
    await _read(PROFILE_UID, bob)

    # Two distinct credentials produce two distinct cold resolutions.
    assert len(env.db.profile_fetch_calls) == 2

    before = len(env.db.profile_fetch_calls)
    await _read(PROFILE_UID, alice)
    await _read(PROFILE_UID, bob)
    assert len(env.db.profile_fetch_calls) == before  # each hit its own entry


@pytest.mark.asyncio
async def test_bot_and_owner_tokens_do_not_share_cache(env):
    owner = _token(VIEWER_UID)
    bot = _token(VIEWER_UID, token_type="bot", account_id=BOT_ACCOUNT_ID)

    await _read(PROFILE_UID, owner)
    # A bot token carries its owner's user_id. Change the stored profile between
    # the two reads: the bot must get its own cold read, not the owner's entry.
    env.db.profile["bio"] = "bot-visible bio"
    bot_result = await _read(PROFILE_UID, bot)

    assert bot_result["profile"]["bio"] == "bot-visible bio"
    assert len(env.db.profile_fetch_calls) == 2  # distinct credential keys


@pytest.mark.asyncio
async def test_authz_decision_is_recomputed_before_cache(env):
    """A relationship change wins over a still-warm cached profile."""
    alice = _token(VIEWER_UID)
    # Owner restricts the profile to friends.
    env.settings.values[(PROFILE_UID, "profile_visibility")] = "friends"
    invalidate_profile_visibility_cache(PROFILE_UID)

    limited = await _read(PROFILE_UID, alice)
    assert "location" not in limited["profile"]
    assert len(env.db.profile_fetch_calls) == 1

    # Alice becomes a friend. No profile write happens, so the cached raw row
    # stays warm -- but authz is recomputed and the full view is returned with
    # NO additional profile query.
    # _is_friend_of(viewer, owner) checks viewer in get_friend_ids(owner).
    env.relationships.friends[PROFILE_UID] = {VIEWER_UID}
    full = await _read(PROFILE_UID, alice)

    assert full["profile"]["location"] == "Mars"
    assert len(env.db.profile_fetch_calls) == 1  # authz fresh, profile still cached


@pytest.mark.asyncio
async def test_profile_mutation_invalidates_cached_reads(env):
    alice = _token(VIEWER_UID)
    first = await _read(PROFILE_UID, alice)
    assert first["profile"]["pronouns"] == "they/them"
    assert len(env.db.profile_fetch_calls) == 1

    # A real profile write changes the stored data and must evict every
    # credential's cached copy of this profile.
    env.db.profile["pronouns"] = "she/her"
    ProfileManager(env.db).update_profile(PROFILE_UID, pronouns="she/her")

    after = await _read(PROFILE_UID, alice)
    assert after["profile"]["pronouns"] == "she/her"
    # The loader re-fetched after invalidation (update_profile's own post-write
    # read plus the route's cold read), so no stale entry survived the write.
    assert len(env.db.profile_fetch_calls) >= 3


def test_invalidation_helper_clears_both_credential_namespaces():
    """The targeted globs match the keys authenticated_cache_key produces."""
    from src.api.routes.users.helpers import authenticated_cache_key

    uid = PROFILE_UID
    builder = authenticated_cache_key(PROFILE_READ_CACHE_PREFIX)
    user_key = builder(user_id=uid, current_user=_token(VIEWER_UID))
    bot_key = builder(
        user_id=uid,
        current_user=_token(VIEWER_UID, token_type="bot", account_id=BOT_ACCOUNT_ID),
    )
    # These are the exact shapes invalidate_profile_read_cache targets.
    assert user_key.endswith(f":user_id:int:{uid}")
    assert bot_key.endswith(f":user_id={uid}")
    # An unrelated target must not be matched by the same glob.
    other_key = builder(user_id=uid + 1, current_user=_token(VIEWER_UID))
    assert not other_key.endswith(f":user_id:int:{uid}")

    from src.core.database.cache.base import mem_cache_set

    mem_cache_set(user_key, {"x": 1}, ttl=60)
    mem_cache_set(bot_key, {"x": 1}, ttl=60)
    mem_cache_set(other_key, {"x": 1}, ttl=60)
    invalidate_profile_read_cache(uid)
    from src.core.database.cache.base import mem_cache_get

    assert mem_cache_get(user_key) is None
    assert mem_cache_get(bot_key) is None
    assert mem_cache_get(other_key) is not None
