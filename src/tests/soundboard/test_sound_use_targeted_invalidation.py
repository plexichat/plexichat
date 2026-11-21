"""PERF regression: a single-sound mutation evicts only that sound's verdicts.

``_can_use_sound`` memoizes decisions under ``(user_id, sound_id, server_id)``.
Both ``delete_sound`` and ``set_sound_permissions`` change the allow-list for
ONE sound, but used to call ``BoundedLRU.clear()`` and drop every user's hot
verdicts for every other sound too.  The fix evicts only the mutated sound's
entries, so unrelated hot entries survive the write.

Before: a one-sound write leaves 0 of N cached verdicts (full clear).
After:  it leaves N - (entries for that sound).
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

SERVER = 1
MUTATED_SOUND = 100
OTHER_SOUND = 200
ROLE = 10
USER = 1
USERS = tuple(range(10))


class _DB:
    def __init__(self):
        self.executed = []
        self.perms = []

    def fetch_one(self, sql, params=()):
        if "srv_roles" in sql:
            return {"server_id": SERVER}
        if "soundboard_permissions" in sql:
            return None  # no existing row -> INSERT branch
        return None

    def fetch_all(self, sql, params=()):
        if "soundboard_permissions" in sql:
            return [dict(p) for p in self.perms]
        return []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

        class Result:
            rowcount = 1

        return Result()


class _Servers:
    def has_permission(self, user_id, server_id, permission):
        return True

    def get_member_roles(self, server_id, user_id):
        return [SimpleNamespace(id=ROLE)]


class _Manager(SoundboardManager):
    def __init__(self):
        self._db = _DB()
        self._servers = _Servers()
        self._sound_use_cache = BoundedLRU(
            max_entries=_SOUND_USE_CACHE_MAX_ENTRIES,
            max_bytes=64 * 1024,
            ttl_seconds=2.0,
        )

    def _generate_id(self):
        return 999


def _sound(sound_id):
    return Sound(id=sound_id, server_id=SERVER, name="s", format=SoundFormat.MP3)


def _warm(m):
    """Prime one verdict per user for two sounds; return the entry count."""
    for sound_id in (MUTATED_SOUND, OTHER_SOUND):
        for user_id in USERS:
            m._sound_use_cache.set((user_id, sound_id, SERVER), True)
    return 2 * len(USERS)


def test_set_sound_permissions_preserves_other_sound_entries():
    m = _Manager()
    total = _warm(m)
    assert len(m._sound_use_cache) == total

    m.get_sound = lambda sid, uid: _sound(MUTATED_SOUND)
    m.set_sound_permissions(USER, MUTATED_SOUND, ROLE, can_use=False)

    # The mutated sound's 10 verdicts are evicted; the other sound's 10 live.
    assert len(m._sound_use_cache) == total - len(USERS)
    assert (USER, OTHER_SOUND, SERVER) in m._sound_use_cache
    assert all((uid, MUTATED_SOUND, SERVER) not in m._sound_use_cache for uid in USERS)


def test_delete_sound_preserves_other_sound_entries():
    m = _Manager()
    total = _warm(m)
    assert len(m._sound_use_cache) == total

    m.get_sound = lambda sid, uid: _sound(MUTATED_SOUND)
    m.delete_sound(USER, MUTATED_SOUND)

    assert len(m._sound_use_cache) == total - len(USERS)
    assert (USER, OTHER_SOUND, SERVER) in m._sound_use_cache
    assert all((uid, MUTATED_SOUND, SERVER) not in m._sound_use_cache for uid in USERS)
