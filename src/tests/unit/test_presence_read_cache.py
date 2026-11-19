"""Bounded, per-credential caching for the single-user presence read route.

``GET /users/{id}/presence`` is a hot poll. On a cold read the core visibility
layer issues two uncached block lookups plus a presence fetch before the
response is serialized, and the REST gate walks the viewer's relationship and
shared-server graph. These tests pin the new contract:

* a warm read is byte-for-byte identical to a cold one but performs no
  visibility/hydration lookup (query reduction);
* the cache key is partitioned per credential and per target, so two users, two
  targets -- and a bot vs. its owner -- never share an entry (no cross-user
  leak);
* the relationship gate runs on *every* request before the cache lookup, so
  denials are never cached and a revoked friendship is not served a warm body;
* a presence update (and a relationship mutation) drops the namespace, so a
  cached body cannot survive a write.

The checks themselves are not weakened: the real route functions, key builder
and cache decorator stay on the path; only the core modules are replaced by
counting fakes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes import presence as presence_routes
from src.api.routes.presence import (
    _PRESENCE_READ_PREFIX,
    get_user_presence,
    update_presence,
)
from src.api.schemas.presence import PresenceUpdate
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache.operations import invalidate_pattern
from src.core.presence.models import CustomStatus, Presence, UserStatus

pytestmark = [pytest.mark.unit]

VIEWER = 42
OTHER = 99
TARGET = 200


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


class _FakePresence:
    """Counting stand-in for the core presence module."""

    def __init__(self):
        # Viewer-specific custom text makes a cross-credential hit observable.
        self.detail_calls: list[tuple[int, int]] = []
        self.status = UserStatus.ONLINE

    def get_visible_presence(self, viewer_id: int, target_id: int) -> Presence:
        self.detail_calls.append((viewer_id, target_id))
        status = (
            UserStatus.OFFLINE if self.status == UserStatus.INVISIBLE else self.status
        )
        return Presence(
            user_id=target_id,
            status=status,
            custom_status=CustomStatus(text=f"note for {viewer_id}", emoji="🎯"),
            activity=None,
            last_seen=1234,
            updated_at=5678,
        )

    # Used by the mutation test (update_presence). These deliberately do not
    # touch ``detail_calls``: only the read route's hydration is counted.
    def set_status(self, user_id: int, status: UserStatus) -> None:
        self.status = status

    def get_presence(self, user_id: int) -> Presence:
        return Presence(user_id=user_id, status=self.status, last_seen=1234)


class _FakeRelationships:
    def __init__(self, friends=()):
        self.friends = set(friends)
        self.friend_calls = 0

    def get_friend_ids(self, viewer_id: int):
        self.friend_calls += 1
        return list(self.friends)


class _FakeServers:
    def __init__(self, shared=()):
        self.shared = set(shared)
        self.shared_calls = 0

    def get_all_shared_member_ids(self, viewer_id: int):
        self.shared_calls += 1
        return list(self.shared)


@pytest.fixture(autouse=True)
def _clean_presence_cache():
    invalidate_pattern(f"{_PRESENCE_READ_PREFIX}:*")
    yield
    invalidate_pattern(f"{_PRESENCE_READ_PREFIX}:*")


@pytest.fixture
def env():
    fake_presence = _FakePresence()
    rel = _FakeRelationships(friends=(TARGET,))
    servers = _FakeServers()
    with (
        patch.object(api, "get_presence", return_value=fake_presence),
        patch.object(api, "get_relationships", return_value=rel),
        patch.object(api, "get_servers", return_value=servers),
    ):
        yield fake_presence, rel, servers


def test_hit_is_identical_and_reduces_queries(env):
    fake_presence, rel, servers = env
    token = _token(VIEWER)

    first = get_user_presence(TARGET, current_user=token)
    second = get_user_presence(TARGET, current_user=token)

    assert first.model_dump() == second.model_dump()
    # One visibility hydration across two reads: the second was cached.
    assert fake_presence.detail_calls == [(VIEWER, TARGET)]
    # The live authz gate still ran on both requests, before the cache lookup.
    assert rel.friend_calls == 2
    assert servers.shared_calls == 0  # friend hit short-circuits the server walk


def test_per_target_keys_do_not_collide(env):
    fake_presence, rel, _ = env
    rel.friends.add(TARGET + 1)
    token = _token(VIEWER)

    get_user_presence(TARGET, current_user=token)
    get_user_presence(TARGET + 1, current_user=token)

    # The target id is part of the key; different targets are different entries.
    assert fake_presence.detail_calls == [(VIEWER, TARGET), (VIEWER, TARGET + 1)]


def test_per_user_keys_do_not_leak(env):
    fake_presence, rel, servers = env
    rel.friends.add(TARGET)
    alice, bob = _token(VIEWER), _token(OTHER)
    # Give Bob a gate path too, via a shared server.
    servers.shared = (TARGET,)

    alice_body = get_user_presence(TARGET, current_user=alice)
    bob_body = get_user_presence(TARGET, current_user=bob)

    assert alice_body.custom_status == "note for 42"
    assert bob_body.custom_status == "note for 99"
    assert len(fake_presence.detail_calls) == 2  # distinct per-viewer keys

    # Repeating each read now hits its own entry; neither crosses over.
    assert get_user_presence(TARGET, current_user=alice).custom_status == "note for 42"
    assert get_user_presence(TARGET, current_user=bob).custom_status == "note for 99"
    assert len(fake_presence.detail_calls) == 2


def test_bot_and_owner_tokens_do_not_share_cache(env):
    fake_presence, _, _ = env
    owner = _token(VIEWER)
    bot = _token(VIEWER, token_type="bot", account_id=777)

    get_user_presence(TARGET, current_user=owner)
    # A bot token carries its owner's user_id. Change what the core returns for
    # that viewer between the calls: the bot must get its own cold read, not the
    # owner's still-warm body.
    fake_presence.status = UserStatus.IDLE
    bot_body = get_user_presence(TARGET, current_user=bot)

    assert bot_body.status == "idle"
    assert len(fake_presence.detail_calls) == 2  # distinct credential keys


def test_denials_are_never_cached_or_served_from_cache(env):
    fake_presence, rel, servers = env
    token = _token(VIEWER)

    # Stranger: no friendship and no shared server.
    rel.friends.clear()
    with pytest.raises(HTTPException) as exc:
        get_user_presence(TARGET, current_user=token)
    assert exc.value.status_code == 404
    assert fake_presence.detail_calls == []  # gate short-circuits the loader

    # Access granted: two reads warm a single entry (the denial was not cached).
    rel.friends.add(TARGET)
    first = get_user_presence(TARGET, current_user=token)
    second = get_user_presence(TARGET, current_user=token)
    assert first.model_dump() == second.model_dump()
    assert len(fake_presence.detail_calls) == 1

    # Access revoked while a warm body still exists: the live gate wins.
    rel.friends.clear()
    with pytest.raises(HTTPException) as exc:
        get_user_presence(TARGET, current_user=token)
    assert exc.value.status_code == 404
    assert len(fake_presence.detail_calls) == 1  # warm body never consulted


@pytest.mark.asyncio
async def test_presence_update_invalidates_cached_read(env):
    fake_presence, _, _ = env
    token = _token(TARGET)

    first = get_user_presence(TARGET, current_user=token)
    assert first.status == "online"
    assert len(fake_presence.detail_calls) == 1

    # A status change through the real mutation route drops the namespace.
    with patch.object(presence_routes, "_get_presence_targets", return_value=[]):
        await update_presence(
            PresenceUpdate(status="idle", custom_status=None, custom_emoji=None),
            current_user=token,
        )

    after = get_user_presence(TARGET, current_user=token)
    assert after.status == "idle"
    assert len(fake_presence.detail_calls) == 2  # cold again after invalidation


def test_relationship_mutation_invalidates_cached_read(env):
    from src.api.routes.relationships.base import RelationshipsBase

    fake_presence, _, _ = env
    token = _token(VIEWER)

    get_user_presence(TARGET, current_user=token)
    assert len(fake_presence.detail_calls) == 1

    RelationshipsBase()._invalidate_relationship_list_cache(VIEWER, TARGET)

    get_user_presence(TARGET, current_user=token)
    assert len(fake_presence.detail_calls) == 2  # cold again after invalidation
