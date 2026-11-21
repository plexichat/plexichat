"""Regression tests for the bounded, epoch-keyed server-owner L0 cache.

The role-hierarchy position check used to issue a fresh
``SELECT owner_id FROM srv_servers`` on every voice moderation decision, even
though the owner is a server-level fact that changes only on an explicit
transfer.  These tests pin that a burst of checks against one server resolves
the owner exactly once (counted on a DB double, not a flaky wall-clock bound),
that an ownership transfer retires the cached value immediately, that a broken
cache fails closed to the authoritative read, and that the store stays bounded
and isolated per backing database.
"""

import pytest

from src.core.servers.channel_access_cache import get_channel_access_cache
from src.core.servers.manager.server_owner_cache import (
    _MISS,
    get_cached_server_owner,
    invalidate_server_owner_cache,
    prime_server_owner,
    server_owner_cache_stats,
)
from src.core.voice.exceptions import PermissionDeniedError
from src.core.voice.manager.moderation import ModerationMixin

SERVER = 5001
MODERATOR, TARGET, OWNER = 200, 300, 100


class _DB:
    """DB double that counts the authoritative owner read."""

    def __init__(self, owner_id=OWNER):
        self.owner_id = owner_id
        self.owner_queries = 0

    def fetch_one(self, sql, params=()):
        if "srv_servers" in sql:
            self.owner_queries += 1
            return {"owner_id": self.owner_id}
        return None


class _Servers:
    def __init__(self, roles_by_user=None):
        self._roles = roles_by_user or {}

    def _get_member_role_rows(self, server_id, user_id):
        return list(self._roles.get(user_id, ()))


class _Moderation(ModerationMixin):
    def __init__(self, db, servers):
        self._db = db
        self._servers = servers


def _moderation(db, moderator_position=5, target_position=1):
    servers = _Servers(
        {
            MODERATOR: [{"position": moderator_position}],
            TARGET: [{"position": target_position}],
        }
    )
    return _Moderation(db, servers)


@pytest.fixture(autouse=True)
def _clean_cache():
    invalidate_server_owner_cache()
    yield
    invalidate_server_owner_cache()


# --------------------------------------------------------------------------- #
# Query-count invariant                                                         #
# --------------------------------------------------------------------------- #


def test_owner_resolved_once_per_server_burst():
    """N hierarchy checks against one server cost one owner read, not N."""
    db = _DB()
    mod = _moderation(db)
    for _ in range(8):
        mod._require_can_moderate_member(
            MODERATOR, TARGET, SERVER, "voice.mute_members"
        )
    assert db.owner_queries == 1


def test_first_check_is_authoritative_and_hit_is_cached():
    db = _DB()
    mod = _moderation(db)
    mod._require_can_moderate_member(MODERATOR, TARGET, SERVER, "voice.mute_members")
    assert db.owner_queries == 1
    assert get_cached_server_owner(db, SERVER) == OWNER
    # A direct cache read does not touch the DB.
    assert db.owner_queries == 1


# --------------------------------------------------------------------------- #
# Fail-closed / invalidation                                                    #
# --------------------------------------------------------------------------- #


def test_cache_miss_recomputes_from_authoritative_db():
    # Nothing primed: the primitive returns the miss sentinel and the caller
    # must fall back to the DB (never infer an owner from the cache).
    db = _DB()
    assert get_cached_server_owner(db, SERVER) is _MISS
    assert db.owner_queries == 0


def test_ownership_transfer_retires_cached_owner():
    db = _DB(owner_id=OWNER)
    mod = _moderation(db)
    # Cached under the current epoch: TARGET is a normal member, allowed.
    mod._require_can_moderate_member(MODERATOR, TARGET, SERVER, "voice.mute_members")
    assert db.owner_queries == 1

    # Ownership moves to TARGET and the RBAC server epoch is bumped (as the
    # real transfer path does).  The old owner id must become unreachable.
    db.owner_id = TARGET
    get_channel_access_cache().bump(SERVER)
    with pytest.raises(PermissionDeniedError):
        mod._require_can_moderate_member(
            MODERATOR, TARGET, SERVER, "voice.mute_members"
        )
    assert db.owner_queries == 2


def test_broken_cache_fails_closed():
    import src.core.servers.manager.server_owner_cache as soc

    db = _DB()
    original = soc._owner_cache.get

    def _boom(*args, **kwargs):
        raise RuntimeError("cache down")

    soc._owner_cache.get = _boom
    try:
        # A cache error is indistinguishable from a miss to the caller.
        assert get_cached_server_owner(db, SERVER) is _MISS
        # The hierarchy path still reaches the authoritative owner read.
        mod = _moderation(db)
        mod._require_can_moderate_member(
            MODERATOR, TARGET, SERVER, "voice.mute_members"
        )
        assert db.owner_queries == 1
    finally:
        soc._owner_cache.get = original


# --------------------------------------------------------------------------- #
# Isolation and bounds                                                          #
# --------------------------------------------------------------------------- #


def test_cache_is_scoped_to_backing_database():
    db_a = _DB(owner_id=11)
    db_b = _DB(owner_id=22)
    prime_server_owner(db_a, SERVER, 11)
    assert get_cached_server_owner(db_a, SERVER) == 11
    # A different database object cannot observe db_a's cached owner.
    assert get_cached_server_owner(db_b, SERVER) is _MISS


def test_cache_is_bounded():
    for sid in range(SERVER, SERVER + 2000):
        prime_server_owner(_DB(), sid, sid)
    stats = server_owner_cache_stats()
    assert stats["size"] <= stats["max_entries"]
