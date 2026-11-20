"""Regression tests for the bounded core emoji catalog/list cache.

The core ``ReactionManager`` resolved the server emoji list (a JOIN with
``auth_users``), single-emoji details and the two COUNT-based quota numbers on
every call.  Dispatch helpers and direct core callers repeated those queries
within one request burst even though the HTTP layer's response cache only
covers the two route bodies.  These tests pin the new contract:

* repeated resolutions of the same key hit the cache (query reduction);
* every custom-emoji mutation invalidates the entries it can change;
* keys are server/flag-scoped and the tier is per-manager, so no cross-server
  or cross-credential reuse of *query results* occurs;
* the cache is hard-bounded (entries, bytes and TTL) and returned values are
  copied, so callers cannot poison it.
"""

from __future__ import annotations

import pytest

from src.core.reactions.manager.emoji_lookup_cache import (
    EmojiCatalogCache,
    DEFAULT_EMOJI_MAX_ENTRIES,
)

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def _manager(monkeypatch, db, servers=None, media=None):
    """Build a manager without the legacy-table migration side effect."""
    from src.core.reactions.manager import ReactionManager

    monkeypatch.setattr(ReactionManager, "_migrate_emoji_table", lambda self: None)
    return ReactionManager(db=db, servers_module=servers, media_module=media)


class _Emoji:
    def __init__(self, emoji_id=1, server_id=10, name="wave", available=True):
        self.id = emoji_id
        self.server_id = server_id
        self.name = name
        self.animated = False
        self.url = "/e/1.png"
        self.size = 100
        self.width = 32
        self.height = 32
        self.created_by = 7
        self.available = available
        self.created_at = 1
        self.uploader_username = "tester"


class _CountingDB:
    """Minimal DB that serves emoji list/detail/count queries by shape."""

    def __init__(self):
        self.rows = {}
        self.calls = []

    def fetch_all(self, sql, params=()):
        self.calls.append(("fetch_all", tuple(params), sql))
        if "GROUP BY animated" in sql:
            sid = params[0]
            grouped = {}
            for r in self.rows.values():
                if r["server_id"] == sid:
                    animated = int(r["animated"])
                    grouped[animated] = grouped.get(animated, 0) + 1
            return [
                {"animated": animated, "count": count}
                for animated, count in sorted(grouped.items())
            ]
        sid = params[0]
        include_unavailable = "available = 1" not in sql
        rows = [
            dict(r)
            for r in self.rows.values()
            if r["server_id"] == sid and (include_unavailable or r["available"])
        ]
        return sorted(rows, key=lambda r: r["name"])

    def fetch_one(self, sql, params=()):
        self.calls.append(("fetch_one", tuple(params)))
        if "SELECT * FROM react_custom_emoji WHERE id = ?" in sql:
            row = self.rows.get(params[0])
            return dict(row) if row else None
        if "COUNT(*) as count" in sql:
            animated = 1 if "animated = 1" in sql else 0
            count = sum(
                1
                for r in self.rows.values()
                if r["server_id"] == params[0] and r["animated"] == animated
            )
            return {"count": count}
        if "SELECT 1 FROM react_custom_emoji WHERE id = ?" in sql:
            return {"1": 1} if params[0] in self.rows else None
        return None

    def execute(self, sql, params=()):
        self.calls.append(("execute", tuple(params)))
        if "INSERT INTO react_custom_emoji" in sql:
            eid, sid, name, animated, url, size, width, height, creator, created = (
                params[:10]
            )
            self.rows[eid] = {
                "id": eid,
                "server_id": sid,
                "name": name,
                "animated": animated,
                "url": url,
                "size": size,
                "width": width,
                "height": height,
                "created_by": creator,
                "available": 1,
                "created_at": created,
                "uploader_username": None,
            }
        elif "UPDATE react_custom_emoji SET name = ?" in sql:
            name, eid = params
            if eid in self.rows:
                self.rows[eid]["name"] = name
        elif "DELETE FROM react_custom_emoji WHERE id = ?" in sql:
            self.rows.pop(params[0], None)
        return _Cursor()

    def begin_transaction(self):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None


class _Cursor:
    rowcount = 1


def _row(emoji):
    return {
        "id": emoji.id,
        "server_id": emoji.server_id,
        "name": emoji.name,
        "animated": int(emoji.animated),
        "url": emoji.url,
        "size": emoji.size,
        "width": emoji.width,
        "height": emoji.height,
        "created_by": emoji.created_by,
        "available": int(emoji.available),
        "created_at": emoji.created_at,
        "uploader_username": emoji.uploader_username,
    }


class TestQueryReduction:
    def test_repeated_resolutions_hit_the_cache(self, monkeypatch):
        db = _CountingDB()
        emoji = _Emoji()
        db.rows[emoji.id] = _row(emoji)
        mgr = _manager(monkeypatch, db)

        # Six reads across three query shapes, all on the same keys.
        for _ in range(2):
            mgr.get_server_custom_emojis(10)
            mgr.get_custom_emoji(1)
            mgr.get_emoji_counts(10)

        # Before the cache: 2 list + 2 detail + 4 count = 8 queries.
        # With the cache and the single grouped COUNT: 1 list + 1 detail +
        # 1 count = 3 (down from 4 when counts used two separate COUNTs).
        assert len(db.calls) == 3
        # One LIST query and one grouped COUNT query; the detail is fetch_one.
        assert sum(1 for c in db.calls if c[0] == "fetch_all") == 2
        assert mgr._emoji_catalog_cache().stats()["hits"] == 3

    def test_emoji_counts_uses_one_grouped_query(self, monkeypatch):
        """Both quota numbers come from one grouped COUNT, not two COUNTs.

        A missing category (no static or no animated rows on the server) must
        still resolve to zero rather than raising on an absent row.
        """
        db = _CountingDB()
        # One static and one animated emoji: both buckets must be reported.
        db.rows[1] = _row(_Emoji(emoji_id=1, server_id=10))
        animated = _Emoji(emoji_id=2, server_id=10)
        animated.animated = True
        db.rows[2] = _row(animated)
        mgr = _manager(monkeypatch, db)

        counts = mgr.get_emoji_counts(10)

        assert counts["static"] == 1
        assert counts["animated"] == 1
        assert len(db.calls) == 1
        assert db.calls[0][0] == "fetch_all"
        assert "GROUP BY animated" in db.calls[0][2]

        # A second read is served from the cache with no further SQL.
        assert mgr.get_emoji_counts(10) == counts
        assert len(db.calls) == 1

    def test_emoji_counts_missing_category_is_zero(self, monkeypatch):
        db = _CountingDB()
        # Server with only static emojis: the animated bucket is absent from
        # the GROUP BY result entirely.
        db.rows[1] = _row(_Emoji(emoji_id=1, server_id=10))
        mgr = _manager(monkeypatch, db)

        counts = mgr.get_emoji_counts(10)

        assert counts["static"] == 1
        assert counts["animated"] == 0
        assert len(db.calls) == 1

    def test_keys_are_server_and_flag_scoped(self, monkeypatch):
        db = _CountingDB()
        db.rows[1] = _row(_Emoji(emoji_id=1, server_id=10))
        db.rows[2] = _row(_Emoji(emoji_id=2, server_id=11))
        mgr = _manager(monkeypatch, db)

        mgr.get_server_custom_emojis(10)
        mgr.get_server_custom_emojis(10, include_unavailable=True)
        mgr.get_server_custom_emojis(11)
        mgr.get_emoji_counts(10)
        mgr.get_emoji_counts(11)

        # Three list keys + two count keys (one grouped COUNT each) -> 5
        # cold queries; no cross-key reuse.
        assert len(db.calls) == 5

    def test_cache_is_per_manager_not_module_global(self, monkeypatch):
        db = _CountingDB()
        db.rows[1] = _row(_Emoji())
        first = _manager(monkeypatch, db)
        second = _manager(monkeypatch, db)

        first.get_server_custom_emojis(10)
        second.get_server_custom_emojis(10)
        assert len(db.calls) == 2

    def test_returned_values_cannot_poison_cache(self, monkeypatch):
        db = _CountingDB()
        db.rows[1] = _row(_Emoji())
        mgr = _manager(monkeypatch, db)

        listing = mgr.get_server_custom_emojis(10)
        listing.clear()
        counts = mgr.get_emoji_counts(10)
        counts["static"] = 999
        detail = mgr.get_custom_emoji(1)
        detail.name = "poisoned"

        assert [e.name for e in mgr.get_server_custom_emojis(10)] == ["wave"]
        assert mgr.get_emoji_counts(10)["static"] == 1
        assert mgr.get_custom_emoji(1).name == "wave"
        # One list + one grouped COUNT + one detail; all three re-reads were hits.
        assert len(db.calls) == 3

    def test_mutating_a_catalog_element_cannot_poison_cache(self, monkeypatch):
        db = _CountingDB()
        db.rows[1] = _row(_Emoji())
        mgr = _manager(monkeypatch, db)

        first = mgr.get_server_custom_emojis(10)
        first[0].name = "poisoned"
        first[0].url = "javascript:alert"

        fresh = mgr.get_server_custom_emojis(10)
        assert fresh[0].name == "wave"
        assert fresh[0].url == "/e/1.png"
        # The catalog query ran once; the second read was a cache hit.
        assert len(db.calls) == 1


class TestInvalidationOnMutation:
    def test_delete_drops_list_and_detail(self, monkeypatch):
        db = _CountingDB()
        emoji = _Emoji()
        db.rows[emoji.id] = _row(emoji)
        mgr = _manager(monkeypatch, db)
        mgr._require_manage_server = lambda *a, **k: None
        mgr._generate_id = lambda: 500
        mgr._get_timestamp = lambda: 1

        assert len(mgr.get_server_custom_emojis(10)) == 1
        assert mgr.get_custom_emoji(1) is not None
        before = len(db.calls)

        mgr.delete_custom_emoji(7, 1)

        assert mgr.get_server_custom_emojis(10) == []
        assert mgr.get_custom_emoji(1) is None
        assert len(db.calls) > before

    def test_update_drops_list_and_detail(self, monkeypatch):
        db = _CountingDB()
        emoji = _Emoji()
        db.rows[emoji.id] = _row(emoji)
        mgr = _manager(monkeypatch, db)
        mgr._require_manage_server = lambda *a, **k: None

        assert mgr.get_server_custom_emojis(10)[0].name == "wave"
        assert mgr.get_custom_emoji(1).name == "wave"

        mgr.update_custom_emoji(7, 1, name="renamed")

        assert mgr.get_server_custom_emojis(10)[0].name == "renamed"
        assert mgr.get_custom_emoji(1).name == "renamed"

    def test_create_drops_list_and_counts(self, monkeypatch):
        db = _CountingDB()
        mgr = _manager(monkeypatch, db)
        mgr._require_manage_server = lambda *a, **k: None
        mgr._validate_emoji_name = lambda name: name
        mgr._generate_id = lambda: 77
        mgr._get_timestamp = lambda: 1
        monkeypatch.setattr(
            "src.core.reactions.manager.emojis.Image.open",
            lambda data: _FakeImage(),
        )

        assert mgr.get_server_custom_emojis(10) == []
        assert mgr.get_emoji_counts(10)["static"] == 0

        created = mgr.create_custom_emoji(7, 10, "new_emoji", b"bytes", "image/png")
        assert created.id == 77

        assert [e.name for e in mgr.get_server_custom_emojis(10)] == ["new_emoji"]
        assert mgr.get_emoji_counts(10)["static"] == 1


class _FakeImage:
    width = 8
    height = 8

    def verify(self):
        return None


class TestCacheBounds:
    def test_entries_and_bytes_are_bounded(self):
        cache = EmojiCatalogCache(ttl_seconds=100, max_entries=2, max_bytes=10_000)
        for i in range(50):
            cache.set(("detail", i), "x" * 10)
        assert cache.stats()["size"] <= 2

    def test_ttl_expiry_never_serves_stale_values(self):
        clock = _Clock()
        cache = EmojiCatalogCache(ttl_seconds=5, clock=clock)
        cache.set(("counts", 10), {"static": 1})
        clock.now += 5
        assert cache.get(("counts", 10), None) is None
        assert cache.stats()["expirations"] == 1

    def test_targeted_invalidation_leaves_other_servers_warm(self):
        clock = _Clock()
        cache = EmojiCatalogCache(ttl_seconds=50, clock=clock)
        cache.set(("list", 10, False), ["a"])
        cache.set(("list", 11, False), ["b"])
        cache.set(("counts", 10), {"static": 1})
        cache.set(("detail", 1), object())

        cache.invalidate_server(10)
        cache.invalidate_emoji(1)

        assert cache.get(("list", 10, False), None) is None
        assert cache.get(("counts", 10), None) is None
        assert cache.get(("detail", 1), None) is None
        assert cache.get(("list", 11, False), None) == ["b"]

    def test_defaults_are_bounded(self):
        assert DEFAULT_EMOJI_MAX_ENTRIES > 0
