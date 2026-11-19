"""Regression guard for the global message-search read path.

The message page handler used to re-check visibility with two queries per hit
(``srv_channels`` lookup + participant/permission resolution), which turned a
25-result page into ~55 DB round trips. The checks are now resolved in bounded
set-based queries. These tests pin three properties:

* results are byte-for-byte identical to the old per-result checks (no leak,
  no over-filtering);
* query cost is independent of the number of hits (no N+1 regression);
* an inaccessible channel/conversation — or a result with neither id — is
  still dropped, and a forged cursor offset cannot force a huge scan.
"""

import base64
import json

import pytest

from src.core.search.indexer.base import MAX_BACKEND_OFFSET
from src.core.search.models import MessageSearchResult


pytestmark = pytest.mark.search


def _result(message_id, *, channel_id=None, conversation_id=0):
    return MessageSearchResult(
        id=message_id,
        message_id=message_id,
        content="needle",
        channel_id=channel_id,
        conversation_id=conversation_id,
    )


def _encode_cursor(offset):
    raw = json.dumps({"kind": "msg", "offset": offset}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _reference_visible(search, user_id, results):
    """The original per-result checks, kept as an oracle for parity."""
    out = []
    for r in results:
        channel_id = getattr(r, "channel_id", None)
        conversation_id = getattr(r, "conversation_id", None)
        if channel_id is not None:
            if search._can_access_channel(user_id, channel_id):
                out.append(r)
        elif conversation_id:
            if search._can_access_conversation(user_id, conversation_id):
                out.append(r)
    return out


class _QueryCounter:
    def __init__(self, db):
        self.db = db
        self.count = 0
        self._orig = (db.fetch_all, db.fetch_one, db.execute)

    def __enter__(self):
        db = self.db

        def wrap(fn):
            def inner(*a, **k):
                self.count += 1
                return fn(*a, **k)

            return inner

        db.fetch_all = wrap(self._orig[0])
        db.fetch_one = wrap(self._orig[1])
        db.execute = wrap(self._orig[2])
        return self

    def __exit__(self, *exc):
        self.db.fetch_all, self.db.fetch_one, self.db.execute = self._orig


@pytest.fixture
def seeded_search(db, auth_manager, server_manager, messaging_manager, search_manager):
    """A user in one readable channel, one DM, and one foreign conversation."""
    auth = auth_manager
    servers = server_manager
    search = search_manager

    owner = auth.register(
        username="hotpath_owner",
        email="hotpath_owner@example.com",
        password="TestPass123!",
    )
    member = auth.register(
        username="hotpath_member",
        email="hotpath_member@example.com",
        password="TestPass123!",
    )
    server = servers.create_server(owner.id, "Hotpath Server")
    servers.add_member(server.id, member.id)

    readable_channel_id, readable_conv_id = None, None
    try:
        created = servers.create_channel(
            owner.id, server.id, "readable", channel_type="text"
        )
        obj = getattr(created, "channel", created)
        readable_channel_id = int(obj.id)
        readable_conv_id = int(getattr(obj, "conversation_id", 0) or 0)
    except Exception:
        # Some server versions create the conversation lazily; fall back to a
        # direct insert so the ACL helpers still have a real row to resolve.
        row = db.fetch_one(
            "SELECT id, conversation_id FROM srv_channels WHERE server_id = ? LIMIT 1",
            (server.id,),
        )
        if row:
            readable_channel_id = int(row["id"])
            readable_conv_id = int(row["conversation_id"] or 0)

    dm = None
    try:
        dm = messaging_manager.create_dm(owner.id, member.id)
    except Exception:
        pass
    dm_conv_id = int(getattr(dm, "id", 0) or 0) if dm else 0

    foreign_conv_id = 987654321  # no msg_participants row for the member

    return {
        "search": search,
        "user_id": member.id,
        "channel_id": readable_channel_id,
        "conv_id": readable_conv_id,
        "dm_conv_id": dm_conv_id,
        "foreign_conv_id": foreign_conv_id,
        "db": db,
    }


@pytest.mark.search
def test_visibility_batching_matches_per_result_parity(seeded_search):
    """Bulk ACL resolution returns exactly the same subset as the old loop."""
    s = seeded_search
    results = [
        _result(1, channel_id=s["channel_id"]),
        _result(2, conversation_id=s["dm_conv_id"]),
        _result(3, conversation_id=s["foreign_conv_id"]),
        _result(4, channel_id=999999999),  # nonexistent channel
        _result(5),  # neither id -> must be dropped
    ]
    assert [
        r.id for r in s["search"]._verify_results_visible(s["user_id"], results)
    ] == [r.id for r in _reference_visible(s["search"], s["user_id"], results)]


@pytest.mark.search
def test_visibility_batching_is_independent_of_hit_count(seeded_search):
    """Doubling/tripling the hit count must not double the query count."""
    s = seeded_search
    search, user_id = s["search"], s["user_id"]

    def count_for(n):
        results = [_result(1000 + i, channel_id=s["channel_id"]) for i in range(n)]
        with _QueryCounter(s["db"]) as counter:
            kept = search._verify_results_visible(user_id, results)
        assert len(kept) == n
        return counter.count

    small = count_for(10)
    large = count_for(120)
    # The old code grew by ~2 queries per additional result; batching adds at
    # most a couple of extra chunk queries across this range.
    assert large - small <= 4, (small, large)


@pytest.mark.search
def test_visibility_still_fails_closed(seeded_search):
    """Security invariant: unreadable / id-less rows are never returned."""
    s = seeded_search
    results = [
        _result(1, channel_id=999999999),
        _result(2, conversation_id=s["foreign_conv_id"]),
        _result(3),
        _result(4, channel_id=s["channel_id"]),
    ]
    kept = s["search"]._verify_results_visible(s["user_id"], results)
    assert [r.id for r in kept] == [4]


@pytest.mark.search
def test_forged_cursor_offset_is_clamped(search_manager):
    """A signed-looking but forged 2**62 offset cannot reach the backend."""
    indexer = search_manager._indexer
    decoded = indexer._decode_offset_cursor(_encode_cursor(2**62), "msg")
    assert decoded["offset"] == MAX_BACKEND_OFFSET
    assert indexer._decode_offset_cursor(_encode_cursor(-50), "msg")["offset"] == 0
