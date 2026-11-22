"""Regression: a block must immediately revoke cached profile read access.

``GET /users/{user_id}/profile`` memoizes the *raw* ``user_profiles`` row in the
per-credential ``user_profile_read_api`` cache and applies the visibility
projection on top of it on every request. The projection gate used to consider
only self / friend / shared-server / visibility -- it never consulted block
relationships. As a result, when A blocked B, B kept reading A's full profile
(bio, location, timezone, custom status, ... ) out of the still-warm cache for
the whole TTL, because neither the gate nor the block write touched that cache.

This test drives the real route, key builder and cache decorator (only the DB /
settings / relationship / server modules are replaced with fakes) and proves:

* a blocked viewer is denied on the very next read even though the raw profile
  is still warm (no re-fetch) -- the live gate, not just an eviction, is what
  makes the block effective;
* unblocking restores the legitimate full read immediately;
* an unrelated viewer's legitimate read is preserved;
* a bot token cannot borrow its human owner's relationship state.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.api as api
from src.api.routes.features import profiles as profiles_routes
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache.operations import invalidate_pattern
from src.core.profiles.manager import PROFILE_READ_CACHE_PREFIX

pytestmark = [pytest.mark.unit]

OWNER = 91001
BLOCKED_VIEWER = 82001
OTHER_VIEWER = 82002
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
    def get_setting(self, user_id, key):
        return None  # unset -> public (the documented default)


class _FakeRelationships:
    def __init__(self):
        # (a, b) means "a blocked b"; is_blocked_by_either is two-way.
        self.blocked_pairs: set[tuple[int, int]] = set()

    def is_blocked_by_either(self, a, b) -> bool:
        return (int(a), int(b)) in self.blocked_pairs or (
            int(b),
            int(a),
        ) in self.blocked_pairs

    def get_friend_ids(self, owner_id):
        return set()

    def get_mutual_servers(self, a, b):
        return False


class _FakeServers:
    """Viewer and owner share a server (the old stale-allowed precondition)."""

    def get_all_shared_member_ids(self, viewer_id):
        return [OWNER]


class _CountingDB:
    def __init__(self, profile: dict):
        self.profile = profile
        self.profile_fetch_calls = []

    def fetch_one(self, sql, params=()):
        if "FROM user_profiles" in sql:
            self.profile_fetch_calls.append(tuple(params))
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
        invalidate_pattern(f"{PROFILE_READ_CACHE_PREFIX}:*")
        invalidate_pattern("profile_visibility:*")
        yield
        invalidate_pattern(f"{PROFILE_READ_CACHE_PREFIX}:*")
        invalidate_pattern("profile_visibility:*")


@pytest.fixture
def env():
    db = _CountingDB(_profile(OWNER))
    relationships = _FakeRelationships()
    with (
        patch.object(api, "get_db", return_value=db),
        patch.object(api, "get_settings", return_value=_FakeSettings()),
        patch.object(api, "get_relationships", return_value=relationships),
        patch.object(api, "get_servers", return_value=_FakeServers()),
    ):
        yield SimpleNamespace(db=db, relationships=relationships)


async def _read(token):
    return await profiles_routes.get_user_profile(OWNER, current_user=token)


@pytest.mark.asyncio
async def test_block_revokes_warm_profile_without_refetch(env):
    """Core regression: a block wins over a still-warm allowed cache entry."""
    blocked = _token(BLOCKED_VIEWER)

    # Warm the raw profile under this viewer's credential: full projection.
    first = await _read(blocked)
    assert first["profile"]["location"] == "Secret Location"
    assert first["profile"]["bio"] == "private bio"
    assert len(env.db.profile_fetch_calls) == 1

    # A blocks B (authoritative state changes; no profile write happens).
    env.relationships.blocked_pairs.add((OWNER, BLOCKED_VIEWER))

    # The very next read must be denied even though the raw row is still warm.
    after = await _read(blocked)
    assert after["profile"]["user_id"] == str(OWNER)
    assert "location" not in after["profile"]
    assert "timezone" not in after["profile"]
    assert "pronouns" not in after["profile"]
    assert "bio" not in after["profile"]
    assert "custom_status_text" not in after["profile"]
    # Proof this is the live gate, not an eviction/re-fetch: the warm entry was
    # reused, so no second profile query was issued.
    assert len(env.db.profile_fetch_calls) == 1


@pytest.mark.asyncio
async def test_unblock_restores_full_read_from_warm_cache(env):
    """An unblock takes effect on the next read without re-fetching either."""
    viewer = _token(BLOCKED_VIEWER)
    assert (await _read(viewer))["profile"]["location"] == "Secret Location"

    env.relationships.blocked_pairs.add((OWNER, BLOCKED_VIEWER))
    assert "location" not in (await _read(viewer))["profile"]

    env.relationships.blocked_pairs.discard((OWNER, BLOCKED_VIEWER))
    restored = await _read(viewer)
    assert restored["profile"]["location"] == "Secret Location"
    assert len(env.db.profile_fetch_calls) == 1


@pytest.mark.asyncio
async def test_block_does_not_affect_other_viewers_or_bot_owner_state(env):
    """Legitimate reads survive; a bot cannot use its owner's relationships."""
    blocked = _token(BLOCKED_VIEWER)
    other = _token(OTHER_VIEWER)
    bot = _token(BLOCKED_VIEWER, token_type="bot", account_id=BOT_ACCOUNT_ID)

    # Baseline: everyone gets the full public profile.
    assert (await _read(blocked))["profile"]["location"] == "Secret Location"
    assert (await _read(other))["profile"]["location"] == "Secret Location"
    assert (await _read(bot))["profile"]["location"] == "Secret Location"

    env.relationships.blocked_pairs.add((OWNER, BLOCKED_VIEWER))

    # Only the blocked user is denied.
    assert "location" not in (await _read(blocked))["profile"]
    assert (await _read(other))["profile"]["location"] == "Secret Location"
    # A bot token carries its owner's user_id, but its viewer id is None at the
    # gate: it must not inherit the (blocked) owner's relationship outcome.
    assert (await _read(bot))["profile"]["location"] == "Secret Location"
