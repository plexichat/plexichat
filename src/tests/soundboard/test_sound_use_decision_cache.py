"""Regression: the sound-usage authorization decision is memoized safely.

``_can_use_sound`` used to run two authoritative queries (the sound's
allow/deny rows and the caller's role set) on *every* playback and UI probe.
Both inputs change only on rare admin actions, so a bounded short-TTL memo
collapses a burst of plays/probes into one read. This test pins the four
safety properties the memo must keep:

* a hit avoids both DB reads (the actual optimization);
* the key carries ``user_id`` -- one caller's verdict is never reused for
  another (no cross-user leak);
* a DENY is cached like an ALLOW (memo never widens access);
* a local permission mutation clears the memo immediately, and the store is
  bounded by the shared ``BoundedLRU`` primitive.

Before: 2 SELECTs per call, forever. After: 2 SELECTs on the first call, 0 on
subsequent hits within the TTL window.
"""

from types import SimpleNamespace

import pytest

from src.core.bounded_lru import BoundedLRU
from src.core.soundboard.manager import (
    _SOUND_USE_CACHE_MAX_ENTRIES,
    SoundboardManager,
)
from src.core.soundboard.models import Sound, SoundFormat

pytestmark = pytest.mark.unit

USER = 1
OTHER = 2
SOUND = 100
SERVER = 1


class _DB:
    """Minimal fetch double: counts permission-row scans."""

    def __init__(self, perms):
        self.perms = [dict(p) for p in perms]
        self.perm_queries = 0

    def fetch_all(self, sql, params=()):
        if "soundboard_permissions" in sql:
            self.perm_queries += 1
            return [dict(p) for p in self.perms]
        return []

    def fetch_one(self, sql, params=()):
        # Role-row / existing-permission lookups used by set_sound_permissions.
        if "srv_roles" in sql:
            return {"server_id": SERVER}
        if "soundboard_permissions" in sql:
            return None
        return None

    def execute(self, sql, params=()):
        class Result:
            rowcount = 1

        return Result()


class _Servers:
    """Minimal role resolver that counts role lookups."""

    def __init__(self, roles):
        self.roles = roles
        self.role_queries = 0

    def get_member_roles(self, server_id, user_id):
        self.role_queries += 1
        return [
            SimpleNamespace(id=rid) for rid in self.roles.get((server_id, user_id), [])
        ]

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


def _manager(perms, roles):
    db = _DB(perms)
    servers = _Servers(roles)
    return _Manager(db, servers), db, servers


def test_repeated_probe_is_served_from_cache():
    m, db, servers = _manager(
        [{"role_id": 10, "can_use": True}], {(SERVER, USER): [10]}
    )
    assert m._can_use_sound(USER, SOUND, SERVER) is True
    assert db.perm_queries == 1
    assert servers.role_queries == 1

    # Second call within the TTL window: no permission scan, no role scan.
    assert m._can_use_sound(USER, SOUND, SERVER) is True
    assert db.perm_queries == 1
    assert servers.role_queries == 1
    assert m._sound_use_cache.stats()["hits"] == 1


def test_cache_key_isolates_users():
    m, db, servers = _manager(
        [{"role_id": 10, "can_use": True}],
        {(SERVER, USER): [10], (SERVER, OTHER): []},
    )
    assert m._can_use_sound(USER, SOUND, SERVER) is True
    # Different user -> different key -> recomputed, never the first verdict.
    assert m._can_use_sound(OTHER, SOUND, SERVER) is False
    assert db.perm_queries == 2


def test_deny_is_cached_too():
    m, db, servers = _manager(
        [{"role_id": 10, "can_use": False}], {(SERVER, USER): [10]}
    )
    assert m._can_use_sound(USER, SOUND, SERVER) is False
    assert m._can_use_sound(USER, SOUND, SERVER) is False
    # A cached False must not be mistaken for a miss and recomputed.
    assert db.perm_queries == 1


def test_before_after_authoritative_reads_per_two_calls():
    """Two decisions cost 4 reads uncached, 1 read cached (warm)."""
    m, db, servers = _manager(
        [{"role_id": 10, "can_use": True}], {(SERVER, USER): [10]}
    )

    # BEFORE: call the authoritative computation directly (no memo).
    m._compute_can_use_sound(USER, SOUND, SERVER)
    m._compute_can_use_sound(USER, SOUND, SERVER)
    assert (db.perm_queries, servers.role_queries) == (2, 2)

    # AFTER: same two calls through the memoized entry point.
    db.perm_queries = 0
    servers.role_queries = 0
    m._can_use_sound(USER, SOUND, SERVER)
    m._can_use_sound(USER, SOUND, SERVER)
    assert (db.perm_queries, servers.role_queries) == (1, 1)


def test_permission_mutation_clears_cached_verdicts():
    m, db, servers = _manager(
        [{"role_id": 10, "can_use": True}], {(SERVER, USER): [10]}
    )
    assert m._can_use_sound(USER, SOUND, SERVER) is True
    assert len(m._sound_use_cache) == 1

    sound = Sound(
        id=SOUND,
        server_id=SERVER,
        name="s",
        format=SoundFormat.MP3,
    )
    m.get_sound = lambda sid, uid: sound
    m.set_sound_permissions(USER, SOUND, 10, can_use=False)

    assert len(m._sound_use_cache) == 0
    db.perms = [{"role_id": 10, "can_use": False}]
    assert m._can_use_sound(USER, SOUND, SERVER) is False
