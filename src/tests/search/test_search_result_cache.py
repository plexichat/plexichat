"""Regression guards for the short-TTL, authz-scoped search result cache.

Repeated identical searches used to replay the entire hot path (backend FTS,
ACL verification, enrichment, ranking) on every request. Final results are now
memoized for a short window with the authz scope baked into the key. These tests
pin three properties:

* **Reduction** — N identical queries issue one backend call, not N.
* **No cross-user leak** — the same query from a different subject (or the same
  subject after a permission change) never reuses another scope's memo.
* **Cache safety** — entries are bounded, expire, are deep-copied, and are
  dropped by ``clear_cache``.

Lightweight fake hosts call the real mixins directly, so these run as fast unit
tests without the full application bootstrap.
"""

import pytest

from src.core.search.manager.message_search import MessageSearchMixin
from src.core.search.manager.result_cache import (
    CACHE_MISS,
    SearchResultCache,
    make_key,
)
from src.core.search.manager.server_search import ServerSearchMixin
from src.core.search.models import MessageSearchResult, ServerSearchResult
from src.core.search.query import QueryParser, RankingEngine

pytestmark = [pytest.mark.unit, pytest.mark.search]


# --- the cache primitive ----------------------------------------------------


def test_cache_hit_miss_and_deep_copy_isolation():
    cache = SearchResultCache(ttl_seconds=10, max_entries=8)
    value = [MessageSearchResult(id=1, message_id=1, content="needle", reactions=["a"])]

    assert cache.get("k") is CACHE_MISS
    cache.put("k", value)
    first = cache.get("k")
    assert first is not CACHE_MISS
    assert first == value

    # Mutating the returned copy must not touch the memoized value.
    first[0].content = "tampered"
    first[0].reactions.append("b")
    again = cache.get("k")
    assert again is not CACHE_MISS
    assert again[0].content == "needle"
    assert again[0].reactions == ["a"]
    assert value[0].content == "needle"


def test_cache_ttl_expires_and_empty_results_are_cacheable():
    cache = SearchResultCache(ttl_seconds=1.0, max_entries=8)
    cache.put("k", [])  # empty list is a valid, cacheable value
    hit = cache.get("k")
    assert hit == [] and hit is not CACHE_MISS

    # Simulate elapsed time without sleeping.
    next(iter(cache._entries.values())).stored_at -= 2.0
    assert cache.get("k") is CACHE_MISS


def test_cache_is_bounded_lru():
    cache = SearchResultCache(ttl_seconds=10, max_entries=3)
    for i in range(6):
        cache.put(i, i)
    assert len(cache) == 3
    # Oldest keys evicted, newest retained.
    assert cache.get(5) == 5
    assert cache.get(0) is CACHE_MISS


def test_disabled_ttl_never_stores():
    cache = SearchResultCache(ttl_seconds=0, max_entries=8)
    cache.put("k", [1])
    assert cache.get("k") is CACHE_MISS
    assert len(cache) == 0


def test_make_key_encodes_authz_scope_and_normalizes_sequences():
    a = make_key("v1", (7, {3, 1, 2}), limit=25, query="x")
    b = make_key("v1", (7, (1, 2, 3)), query="x", limit=25)
    other_user = make_key("v1", (8, (1, 2, 3)), query="x", limit=25)
    assert a == b  # same scope/params regardless of order/type
    assert a != other_user  # different subject -> different key


# --- fake mixin hosts --------------------------------------------------------


class _FakeMessageIndexer:
    def __init__(self):
        self.calls = 0
        self.last_conversation_ids = None

    def search_messages(self, *, query, conversation_ids, **kwargs):
        self.calls += 1
        self.last_conversation_ids = list(conversation_ids)
        # Echo the caller's readable set into the result so a leak is visible.
        conv = conversation_ids[0] if conversation_ids else 0
        return [
            MessageSearchResult(
                id=conv, message_id=conv, content="needle", conversation_id=conv
            )
        ]


class _MessageHost(MessageSearchMixin):
    def __init__(self, accessible_by_user):
        self._config = {"result_limit": 100}
        self._indexer = _FakeMessageIndexer()
        self._query_parser = QueryParser()
        self._ranking_engine = RankingEngine()
        self._accessible_by_user = accessible_by_user
        self._search_rate_window_started_ms = {}
        self._search_rate_count = {}

    def _check_rate_limit(self, user_id):
        pass

    def _get_accessible_conversations(
        self, user_id, conversation_id=None, server_id=None, channel_id=None
    ):
        return list(self._accessible_by_user.get(user_id, ()))

    # The mixin calls self._filter_processor.apply_filters(...)
    class _FilterProcessor:
        def apply_filters(self, results, parsed, user_id):
            return results

    _filter_processor = _FilterProcessor()

    def _verify_results_visible(self, user_id, results, access_cache=None):
        return results

    def _enrich_message_results(self, results, user_id):
        return results

    def _get_timestamp(self):
        return 1_700_000_000


class _FakeServerIndexer:
    def __init__(self):
        self.calls = 0

    def search_servers(self, *, query, category, public_only, limit, offset):
        self.calls += 1
        return [ServerSearchResult(id=1, server_id=1, name="Server " + query)]

    def search_servers_page(self, *, query, category, public_only, limit, cursor):
        self.calls += 1
        return [ServerSearchResult(id=1, server_id=1, name="Server " + query)], None


class _ServerHost(ServerSearchMixin):
    def __init__(self):
        self._config = {"result_limit": 100}
        self._indexer = _FakeServerIndexer()
        self._ranking_engine = RankingEngine()
        self._search_rate_window_started_ms = {}
        self._search_rate_count = {}

    def _check_rate_limit(self, user_id):
        pass

    def _enrich_server_results(self, results):
        return results

    def _get_timestamp(self):
        return 1_700_000_000


# --- reduction --------------------------------------------------------------


def test_identical_message_queries_collapse_to_one_backend_call():
    host = _MessageHost({1: (7,)})
    n = 30
    for _ in range(n):
        results = host.search_messages(user_id=1, query="needle", limit=10)
        assert [r.id for r in results] == [7]
    assert host._indexer.calls == 1, (
        f"{n} identical searches made {host._indexer.calls} backend calls"
    )


def test_identical_server_queries_collapse_to_one_backend_call():
    host = _ServerHost()
    for _ in range(30):
        host.search_servers(user_id=1, query="needle", limit=10)
    assert host._indexer.calls == 1


def test_distinct_queries_still_miss_separately():
    host = _MessageHost({1: (7,)})
    for i in range(5):
        host.search_messages(user_id=1, query=f"needle-{i}", limit=10)
    assert host._indexer.calls == 5


# --- no cross-user leak -----------------------------------------------------


def test_same_query_different_users_do_not_share_results():
    # Users 1 and 2 can read disjoint conversations. The backend echoes the
    # readable conversation into each result, so a scope-blind cache would
    # hand user 2 the entry computed for user 1.
    host = _MessageHost({1: (7,), 2: (8,)})
    first = host.search_messages(user_id=1, query="needle", limit=10)
    second = host.search_messages(user_id=2, query="needle", limit=10)
    assert [r.id for r in first] == [7]
    assert [r.id for r in second] == [8]
    assert host._indexer.calls == 2  # one per authz scope, not shared


def test_permission_change_changes_authz_scope_and_misses():
    host = _MessageHost({1: (7,)})
    assert [r.id for r in host.search_messages(1, "needle", limit=10)] == [7]

    # Same user, scoped to a different conversation (permission snapshot
    # changed) — the key includes the readable set, so no stale reuse.
    host._accessible_by_user[1] = (8,)
    assert [r.id for r in host.search_messages(1, "needle", limit=10)] == [8]
    assert host._indexer.calls == 2


def test_server_search_does_not_leak_across_users():
    host = _ServerHost()
    host.search_servers(user_id=1, query="needle", limit=10)
    host.search_servers(user_id=2, query="needle", limit=10)
    assert host._indexer.calls == 2


# --- invalidation / hygiene -------------------------------------------------


def test_clear_cache_forces_recomputation():
    host = _MessageHost({1: (7,)})
    host.search_messages(1, "needle", limit=10)
    host.search_messages(1, "needle", limit=10)
    assert host._indexer.calls == 1

    host.clear_cache()
    host.search_messages(1, "needle", limit=10)
    assert host._indexer.calls == 2


def test_cache_respects_config_ttl_zero():
    host = _MessageHost({1: (7,)})
    host._config["result_cache_ttl_seconds"] = 0
    for _ in range(3):
        host.search_messages(1, "needle", limit=10)
    assert host._indexer.calls == 3
