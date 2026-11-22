"""Regression coverage and before/after measurement for the voice lookup cache.

``get_voice_state`` is called on every ``VOICE_SPEAKING`` frame, and
``_get_server_channel`` / ``_get_channel_settings`` on every join, move and
channel-detail read.  They used to issue a fresh ``SELECT`` each time.  These
tests pin the three properties that make the bounded cache safe:

* **Query reduction** - a burst of N identical reads collapses to one query
  (the uncached loader is used as the "before" reference).
* **Invalidation** - every voice-manager write drops the affected entries, so
  a read-after-write observes the new value, not a stale one.
* **Bounds / TTL** - entry count is capped, expired entries are never served,
  and a full clear drops everything.
"""

import time

from src.core.voice.manager import VoiceManager
from src.core.voice.manager.cache import VoiceLookupCache, _MISS

USER_ID = 42
CHANNEL_ID = 777
SERVER_ID = 9


class _CountingDB:
    """DB double that counts SELECTs and mutates state on the writes we use."""

    type = "sqlite"

    def __init__(self):
        self.state = {
            "user_id": USER_ID,
            "channel_id": CHANNEL_ID,
            "server_id": SERVER_ID,
            "self_mute": 0,
            "self_deaf": 0,
            "server_mute": 0,
            "server_deaf": 0,
            "suppress": 0,
            "streaming": 0,
            "video": 0,
            "joined_at": 1,
            "last_activity": 1,
        }
        self.channel = {
            "id": CHANNEL_ID,
            "server_id": SERVER_ID,
            "name": "chan",
            "channel_type": "voice",
            "position": 0,
            "deleted": 0,
        }
        self.settings = {}
        self.fetch_one_calls = []
        self.executes = []

    def fetch_one(self, sql, params):
        self.fetch_one_calls.append((sql, tuple(params)))
        if "FROM voice_states" in sql:
            return dict(self.state) if self.state else None
        if "FROM srv_channels" in sql:
            return dict(self.channel) if self.channel else None
        if "FROM voice_channel_settings" in sql:
            row = self.settings.get(params[0])
            return dict(row) if row else None
        raise AssertionError(f"unexpected fetch_one SQL: {sql}")

    def execute(self, sql, params):
        self.executes.append((sql, tuple(params)))
        if "SET self_mute = ?" in sql:
            self.state["self_mute"] = params[0]
        if "SET bitrate = ?" in sql:
            self.settings[params[1]] = {
                "channel_id": params[1],
                "user_limit": 0,
                "bitrate": params[0],
                "region_id": None,
            }

    def insert_or_ignore(self, table, columns, values):
        pass


def _make_manager(db=None):
    db = db or _CountingDB()
    manager = object.__new__(VoiceManager)
    manager._db = db
    manager._servers = object()  # truthy: channel lookups are authorized
    manager._auth = None
    return manager, db


def _state_selects(db):
    return [c for c in db.fetch_one_calls if "FROM voice_states" in c[0]]


# --- before / after --------------------------------------------------------


def test_before_after_query_reduction(capsys):
    """N speaking-frame reads: N queries before, 1 query after caching."""
    manager, db = _make_manager()
    n = 500

    # BEFORE: the authoritative, uncached loader every frame used to call.
    t0 = time.perf_counter()
    for _ in range(n):
        manager._load_voice_state(USER_ID)
    before_seconds = time.perf_counter() - t0
    before_queries = len(_state_selects(db))

    db.fetch_one_calls.clear()
    manager.clear_voice_lookup_cache()

    # AFTER: the cached public read path used by VOICE_SPEAKING.
    t0 = time.perf_counter()
    for _ in range(n):
        manager.get_voice_state(USER_ID)
    after_seconds = time.perf_counter() - t0
    after_queries = len(_state_selects(db))

    with capsys.disabled():
        print(
            f"\nvoice lookup before/after ({n} reads):\n"
            f"  before: {before_queries} state SELECTs, {before_seconds * 1000:.3f} ms\n"
            f"  after:  {after_queries} state SELECTs, {after_seconds * 1000:.3f} ms"
        )

    assert before_queries == n
    assert after_queries == 1
    stats = manager._get_voice_lookup_cache().stats()
    assert stats["hits"] == n - 1
    assert stats["misses"] == 1


def test_channel_and_settings_reads_are_cached():
    """Repeated channel-metadata reads issue one SELECT each, not N."""
    manager, db = _make_manager()
    db.settings[CHANNEL_ID] = {
        "channel_id": CHANNEL_ID,
        "user_limit": 12,
        "bitrate": 96000,
        "region_id": "eu",
    }

    for _ in range(20):
        channel = manager._get_server_channel(CHANNEL_ID)
        settings = manager._get_channel_settings(CHANNEL_ID)

    assert channel["name"] == "chan"
    assert settings["user_limit"] == 12
    assert len([c for c in db.fetch_one_calls if "FROM srv_channels" in c[0]]) == 1
    assert (
        len([c for c in db.fetch_one_calls if "FROM voice_channel_settings" in c[0]])
        == 1
    )


# --- invalidation ----------------------------------------------------------


def test_state_write_invalidates_cached_state():
    """A setter's read-after-write observes the new DB value."""
    manager, db = _make_manager()

    first = manager.get_voice_state(USER_ID)
    assert first.self_mute is False
    # Warm the cache further.
    assert manager.get_voice_state(USER_ID).self_mute is False

    updated = manager.set_self_mute(USER_ID, True)
    assert updated.self_mute is True
    assert db.state["self_mute"] == 1
    # The post-write read re-queried and will keep returning the new value.
    assert manager.get_voice_state(USER_ID).self_mute is True


def test_explicit_user_invalidation_forces_requery():
    manager, db = _make_manager()
    manager.get_voice_state(USER_ID)
    assert len(_state_selects(db)) == 1

    manager.get_voice_state(USER_ID)
    assert len(_state_selects(db)) == 1  # cache hit

    manager._invalidate_voice_state(USER_ID)
    manager.get_voice_state(USER_ID)
    assert len(_state_selects(db)) == 2  # cache miss after invalidation


def test_channel_write_invalidates_cached_settings():
    manager, db = _make_manager()
    db.settings[CHANNEL_ID] = {
        "channel_id": CHANNEL_ID,
        "user_limit": 0,
        "bitrate": 64000,
        "region_id": None,
    }
    assert manager._get_channel_settings(CHANNEL_ID)["bitrate"] == 64000

    # Simulate the settings write path (execute + invalidate) directly: the
    # manager's set_bitrate needs a permission source, so drive the primitives.
    db.execute(
        "UPDATE voice_channel_settings SET bitrate = ? WHERE channel_id = ?",
        (96000, CHANNEL_ID),
    )
    manager._invalidate_channel_lookup(CHANNEL_ID)

    assert manager._get_channel_settings(CHANNEL_ID)["bitrate"] == 96000
    settings_selects = [
        c for c in db.fetch_one_calls if "FROM voice_channel_settings" in c[0]
    ]
    assert len(settings_selects) == 2


def test_negative_result_is_cached():
    """A 'not in voice' result is cached, so non-member frames stay cheap."""
    manager, db = _make_manager()
    db.state = None
    for _ in range(10):
        assert manager.get_voice_state(USER_ID) is None
    assert len(_state_selects(db)) == 1


# --- cache primitive -------------------------------------------------------


def test_bounded_lru_evicts_oldest():
    clock = [0.0]
    cache = VoiceLookupCache(ttl_seconds=10.0, max_entries=2, clock=lambda: clock[0])
    key_a, key_b, key_c = ("state", 1), ("state", 2), ("state", 3)
    cache.set(key_a, "a")
    cache.set(key_b, "b")
    cache.get(key_a)  # a is now most-recently-used
    cache.set(key_c, "c")  # evicts b

    assert cache.get(key_b) is _MISS
    assert cache.get(key_a) == "a"
    assert cache.get(key_c) == "c"
    assert cache.stats()["evictions"] == 1
    assert len(cache) == 2


def test_ttl_expiry_never_serves_stale_value():
    clock = [0.0]
    cache = VoiceLookupCache(ttl_seconds=1.0, max_entries=8, clock=lambda: clock[0])
    cache.set(("state", 1), "fresh")
    assert cache.get(("state", 1)) == "fresh"

    clock[0] = 1.01
    assert cache.get(("state", 1)) is _MISS
    assert cache.stats()["expirations"] == 1


def test_clear_drops_everything():
    cache = VoiceLookupCache()
    for i in range(5):
        cache.set(("state", i), i)
    assert len(cache) == 5
    cache.clear()
    assert len(cache) == 0
    assert cache.get(("state", 0)) is _MISS
