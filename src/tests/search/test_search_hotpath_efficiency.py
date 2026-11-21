"""Regression guards for two search hot-path inefficiencies.

1. ``PostgresIndexer`` used to probe ``websearch_to_tsquery`` support once per
   search (six entry points), a needless DB round-trip on every request. The
   tri-state answer is now cached for the connection.
2. ``search_messages_page`` re-ran the same bulk channel/conversation ACL
   queries for every backend page. A *request-scoped* memo resolves each id
   once per search, keyed to the user so a decision can never cross users or
   outlive the permission snapshot.

These tests pin result parity, the no-cross-user-leak invariant, and that work
stays bounded as hits/pages grow. They use lightweight fakes so they run as
fast unit tests without the full application bootstrap.
"""

import pytest

from src.core.search.indexer.postgres import PostgresIndexer, _escape_like
from src.core.search.manager.access import AccessControlMixin
from src.core.search.models import MessageSearchResult

pytestmark = [pytest.mark.unit, pytest.mark.search]


# --- websearch capability probe -------------------------------------------


class _CountingFakeDB:
    def __init__(self):
        self.probe_calls = 0
        self.selects = []

    def execute(self, query, params=None):
        return None

    def fetch_one(self, query, params=None):
        if "SELECT 1" in query and "websearch_to_tsquery" in query:
            self.probe_calls += 1
        return {"?": 1}

    def fetch_all(self, query, params=None):
        self.selects.append((query, params))
        return []


def test_websearch_support_probe_runs_once_per_indexer():
    db = _CountingFakeDB()
    indexer = PostgresIndexer(db)

    for _ in range(25):
        # Every search entry point consults the capability.
        indexer.search_messages("needle")
        indexer.search_users("needle")
        indexer.search_servers("needle")

    assert db.probe_calls == 1


def test_websearch_probe_cache_result_parity_and_reset():
    # Cached value must be identical to the un-cached one for both branches,
    # and close() must invalidate so a re-opened indexer re-probes.
    supported = _CountingFakeDB()
    idx = PostgresIndexer(supported)
    assert idx._check_websearch_support() is True
    assert idx._check_websearch_support() is True
    assert supported.probe_calls == 1
    idx.close()
    assert idx._check_websearch_support() is True
    assert supported.probe_calls == 2

    class _Unsupported(_CountingFakeDB):
        def fetch_one(self, query, params=None):
            if "websearch_to_tsquery" in query:
                self.probe_calls += 1
                raise RuntimeError("unsupported function")
            return None

    unsupported = _Unsupported()
    idx2 = PostgresIndexer(unsupported)
    assert idx2._check_websearch_support() is False
    assert idx2._check_websearch_support() is False
    assert unsupported.probe_calls == 1

    # Result parity: with the probe cached, query construction is unchanged —
    # the websearch branch is still selected and LIKE is still escaped.
    db3 = _CountingFakeDB()
    idx3 = PostgresIndexer(db3)
    idx3.initialize()
    idx3.search_messages("%")  # first call primes the probe cache
    idx3.search_messages("%")  # second call reuses it
    sql, params = db3.selects[-1]
    assert "websearch_to_tsquery" in sql
    assert params[1] == f"%{_escape_like('%')}%"


# --- paged ACL memo -------------------------------------------------------

_USER_CHANNELS = {
    1: {42},  # user 1 may read channel 42 only
    2: {43},  # user 2 may read channel 43 only
}
_USER_CONVERSATIONS = {
    1: {7},  # user 1 may read conversation 7 only
    2: {8},  # user 2 may read conversation 8 only
}


class _ACLManager:
    """Fake manager with a simple, per-user ACL and bulk-call counters."""

    def __init__(self):
        self.channel_calls = 0
        self.conversation_calls = 0

    def _can_access_channels_bulk(self, user_id, channel_ids):
        self.channel_calls += 1
        allowed = _USER_CHANNELS.get(user_id, set())
        return {cid: cid in allowed for cid in channel_ids}

    def _can_access_conversations_bulk(self, user_id, conversation_ids):
        self.conversation_calls += 1
        allowed = _USER_CONVERSATIONS.get(user_id, set())
        return {cid: cid in allowed for cid in conversation_ids}

    _verify_results_visible = AccessControlMixin._verify_results_visible
    _as_id = staticmethod(AccessControlMixin._as_id)


def _hit(message_id, channel_id=None, conversation_id=None):
    return MessageSearchResult(
        id=message_id,
        message_id=message_id,
        content="needle",
        channel_id=channel_id,
        conversation_id=conversation_id,
    )


def test_acl_memo_result_parity():
    """Memoized verification returns exactly the same subset as un-memoized."""
    hits = [
        _hit(1, channel_id=42),  # user 1: allowed
        _hit(2, channel_id=43),  # user 1: denied
        _hit(3, conversation_id=7),  # user 1: allowed
        _hit(4, conversation_id=8),  # user 1: denied
        _hit(5, channel_id=999),  # nonexistent -> denied
        _hit(6),  # neither id -> dropped (fail closed)
    ]
    mgr = _ACLManager()
    baseline = [r.id for r in mgr._verify_results_visible(1, hits)]
    memoized = [r.id for r in mgr._verify_results_visible(1, hits, {})]
    assert memoized == baseline == [1, 3]


def test_acl_memo_does_not_leak_across_users():
    """A memo primed for user 1 must not grant user 2 user 1's decisions."""
    hits = [_hit(1, channel_id=42), _hit(2, channel_id=43)]
    mgr = _ACLManager()
    shared_memo = {}

    assert [r.id for r in mgr._verify_results_visible(1, hits, shared_memo)] == [1]
    # Reusing the same memo for user 2 must re-resolve under user 2's ACL.
    assert [r.id for r in mgr._verify_results_visible(2, hits, shared_memo)] == [2]


def test_acl_memo_work_is_bounded_across_pages():
    """Repeated ids across pages are resolved once; cost is hit-count flat."""
    mgr = _ACLManager()

    # Simulate eight backend pages all hitting the same recurring ids.
    memo = {}
    calls_before = mgr.channel_calls + mgr.conversation_calls
    for page in range(8):
        hits = [_hit(page * 100 + i, channel_id=42) for i in range(25)]
        kept = mgr._verify_results_visible(1, hits, memo)
        assert len(kept) == 25
    added = (mgr.channel_calls + mgr.conversation_calls) - calls_before
    # One channel bulk call for the whole request, not eight.
    assert added == 1

    # Doubling the hits in a single page must not double the bulk queries.
    fresh = _ACLManager()
    fresh._verify_results_visible(1, [_hit(i, channel_id=42) for i in range(10)], {})
    small = fresh.channel_calls
    fresh._verify_results_visible(1, [_hit(i, channel_id=42) for i in range(120)], {})
    assert fresh.channel_calls - small == 1


def test_acl_memo_still_fails_closed():
    """Unreadable / malformed / id-less rows are never returned, cached or not."""
    hits = [
        _hit(1, channel_id=999999999),
        _hit(2, conversation_id=999999999),
        _hit(3),
        _hit(4, channel_id="not-an-int"),
        _hit(5, channel_id=42),
    ]
    mgr = _ACLManager()
    assert [r.id for r in mgr._verify_results_visible(1, hits, {})] == [5]
