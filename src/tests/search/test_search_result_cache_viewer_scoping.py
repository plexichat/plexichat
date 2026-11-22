"""Search result caches are partitioned by the authenticated viewer.

Security proof for the short-TTL search result cache. The core question these
tests answer:

    Can user A's search results (message bodies from a private channel only A
    can read) be served to user B via an identical query hitting A's warm cache
    entry?

The answer must be NO. The result cache key is ``(version, authz_scope, params)``
and every caller folds the subject ``user_id`` into ``authz_scope`` (alongside
the live-resolved readable conversations / server-member scope). The
authorization gate runs on every request *before* the cache is consulted, so a
denied caller never reaches a cached entry at all.

These tests are non-vacuous: each exercises the real ``MessageSearchMixin``
pipeline against a fake host with observable private-body assertions. The last
test is the direct proof that the viewer is in the key — it fails if the viewer
is dropped (a query-only key). The first two pin the practical leak scenarios:
a disjoint private-channel set still cannot reuse A's warm entry, and a caller
with no readable set is rejected by the live gate before any cache lookup.
"""

import pytest

from src.core.search.manager.message_search import MessageSearchMixin
from src.core.search.manager.result_cache import get_result_cache
from src.core.search.models import MessageSearchResult
from src.core.search.query import QueryParser, RankingEngine

pytestmark = [pytest.mark.unit, pytest.mark.search]


class _BodiedIndexer:
    """Echo each readable conversation as an unmistakable private body."""

    def __init__(self):
        self.calls = 0

    def search_messages(self, *, query, conversation_ids, **kwargs):
        self.calls += 1
        return [
            MessageSearchResult(
                id=cid,
                message_id=cid,
                content=f"PRIVATE-BODY-OF-CONV-{cid}",
                conversation_id=cid,
            )
            for cid in conversation_ids
        ]


class _Host(MessageSearchMixin):
    def __init__(self, accessible_by_user):
        self._config = {"result_limit": 100}
        self._indexer = _BodiedIndexer()
        self._query_parser = QueryParser()
        self._ranking_engine = RankingEngine()
        self._accessible = accessible_by_user
        self._search_rate_window_started_ms = {}
        self._search_rate_count = {}

    def _check_rate_limit(self, user_id):
        pass

    def _get_accessible_conversations(
        self, user_id, conversation_id=None, server_id=None, channel_id=None
    ):
        return list(self._accessible.get(user_id, ()))

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


def test_warm_private_entry_from_user_a_is_not_served_to_user_b():
    """User A warms the cache with a private body; B's identical query must
    not receive it."""
    host = _Host({1: (7,), 2: (8,)})  # disjoint private channels

    first = host.search_messages(user_id=1, query="secret", limit=10)
    # Non-vacuous precondition: A really did get a private body cached.
    assert any("CONV-7" in r.content for r in first)

    second = host.search_messages(user_id=2, query="secret", limit=10)
    assert not any("CONV-7" in r.content for r in second)
    assert all("CONV-8" in r.content for r in second)
    # One backend computation per viewer scope, never a shared entry.
    assert host._indexer.calls == 2


def test_no_access_viewer_cannot_read_a_warm_private_entry():
    """The live readable-set gate runs before the cache: a viewer with no
    access returns empty rather than reusing A's warm entry."""
    host = _Host({1: (7,), 2: ()})  # user 2 can read nothing

    first = host.search_messages(user_id=1, query="secret", limit=10)
    assert any("CONV-7" in r.content for r in first)

    second = host.search_messages(user_id=2, query="secret", limit=10)
    assert second == []
    assert not any("CONV-7" in r.content for r in second)
    # A's query computed once; B was rejected by the live gate before any
    # cache lookup or backend call.
    assert host._indexer.calls == 1


def test_identical_readable_sets_still_partition_by_viewer():
    """Even when two viewers can read exactly the same conversations, the key
    still carries the subject id: one viewer's computed/enriched page is never
    handed to the other. This is the direct proof that the key is partitioned
    by the authenticated viewer, not merely by query/resource params."""
    host = _Host({1: (7,), 2: (7,)})  # same conv, different subject

    host.search_messages(user_id=1, query="secret", limit=10)
    host.search_messages(user_id=2, query="secret", limit=10)

    # Two distinct cache entries despite identical query and scope params.
    cache = get_result_cache(host, "messages")
    assert len(cache) == 2
    assert host._indexer.calls == 2

    # Structural proof: the authenticated viewer is the first element of each
    # key's authz scope (``make_key`` returns ``(version, scope, params)``).
    keys = list(cache._entries.keys())
    assert {key[1][0] for key in keys} == {1, 2}
