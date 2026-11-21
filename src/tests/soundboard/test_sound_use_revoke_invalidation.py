"""Regression: a role/membership revoke must retire a cached sound-use verdict.

``_can_use_sound`` memoizes the ALLOW/DENY boolean for a short TTL. That
boolean depends on the caller's role set, which the servers module mutates on
role assignment/removal, role deletion, kick/ban and ownership transfer — none
of which go through ``set_sound_permissions``. The memo used to key only on
``(user, sound, server)`` and ignored the RBAC epoch that every such mutation
bumps, so a member whose granting role was revoked kept replaying a warm
ALLOW until the 2s TTL elapsed. The verdict is now tagged with the epoch it
was computed under and rejected as soon as that epoch advances.
"""

from types import SimpleNamespace

import pytest

from src.core.bounded_lru import BoundedLRU
from src.core.servers.channel_access_cache import get_channel_access_cache
from src.core.soundboard.manager import (
    _SOUND_USE_CACHE_MAX_ENTRIES,
    SoundboardManager,
)

pytestmark = pytest.mark.unit

USER = 7
SOUND = 700
# Unique server id so the global epoch bump below cannot collide with another
# test's scope when the suite runs in a shared process.
SERVER = 7_000_007


class _DB:
    """Minimal fetch double: the sound's permission rows come from ``self.perms``."""

    def __init__(self, perms):
        self.perms = [dict(p) for p in perms]
        self.perm_queries = 0

    def fetch_all(self, sql, params=()):
        if "soundboard_permissions" in sql:
            self.perm_queries += 1
            return [dict(p) for p in self.perms]
        return []

    def fetch_one(self, sql, params=()):
        return None

    def execute(self, sql, params=()):
        class Result:
            rowcount = 1

        return Result()


class _Servers:
    """Role resolver whose answer can change to model a live revoke."""

    def __init__(self, roles):
        self.roles = list(roles)

    def get_member_roles(self, server_id, user_id):
        return [SimpleNamespace(id=rid) for rid in self.roles]

    def has_permission(self, user_id, server_id, permission):
        return True


class _Manager(SoundboardManager):
    def __init__(self, db, servers):
        self._db = db
        self._servers = servers
        self._sound_use_cache = BoundedLRU(
            max_entries=_SOUND_USE_CACHE_MAX_ENTRIES,
            max_bytes=64 * 1024,
            ttl_seconds=2.0,
        )


def _manager():
    # Role 10 is the only ALLOW entry for this sound.
    db = _DB([{"role_id": 10, "can_use": True}])
    servers = _Servers([10])
    return _Manager(db, servers), db, servers


def test_role_revoke_retires_cached_allow():
    m, db, servers = _manager()

    # First probe computes from the authoritative source and caches ALLOW.
    assert m._can_use_sound(USER, SOUND, SERVER) is True
    assert db.perm_queries == 1
    # A repeat probe is served from the warm memo.
    assert m._can_use_sound(USER, SOUND, SERVER) is True
    assert db.perm_queries == 1

    # Admin removes the granting role and the servers module bumps the RBAC
    # epoch (exactly what role removal does). The user's role set no longer
    # contains the allowed role, so a fresh decision must be DENY.
    servers.roles = []
    get_channel_access_cache().bump(SERVER, USER)

    assert m._can_use_sound(USER, SOUND, SERVER) is False
    # The old ALLOW was rejected on epoch mismatch, not replayed from cache.
    assert db.perm_queries == 2


def test_fresh_verdict_is_cached_under_new_epoch():
    """The fix must not simply disable the memo: post-revoke hits stay cached."""
    m, db, servers = _manager()

    servers.roles = []
    get_channel_access_cache().bump(SERVER, USER)
    assert m._can_use_sound(USER, SOUND, SERVER) is False
    assert db.perm_queries == 1

    assert m._can_use_sound(USER, SOUND, SERVER) is False
    assert db.perm_queries == 1
