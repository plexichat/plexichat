"""Core-level L0 cache for reaction aggregation reads.

The HTTP layer memoizes the single-message reaction body, but message-list
hydration (``get_reactions_batch``), count helpers and paginated reactor lists
still ran their expensive ``GROUP BY`` / ``COUNT(*)`` queries on every call.
This module memoizes those *query results* for a short window, keyed by viewer
(and optionally credential), with the live authz gate kept in front of the
cache and invalidation on every mutation.

These tests pin four properties, without weakening any existing check:

* parity -- a warm read is identical to a cold read;
* query reduction -- the warm read does not re-run the aggregation query;
* no cross-user / cross-credential leak -- block filtering and the ``me`` flag
  are viewer-specific, and credential discriminators partition the namespace;
* invalidation -- every reaction and emoji mutation drops the warm body.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.core.reactions.exceptions import MessageNotFoundError
from src.core.reactions.manager.cache import (
    ReactionReadCache,
    reaction_read_key,
)


@contextmanager
def _count_queries(db):
    """Record every SQL statement issued through the real DB adapter."""
    calls: dict[str, list[str]] = {"all": [], "one": []}
    orig_all, orig_one = db.fetch_all, db.fetch_one

    def counted_all(query, params=None):
        calls["all"].append(query or "")
        return orig_all(query, params)

    def counted_one(query, params=None):
        calls["one"].append(query or "")
        return orig_one(query, params)

    db.fetch_all = counted_all
    db.fetch_one = counted_one
    try:
        yield calls
    finally:
        db.fetch_all, db.fetch_one = orig_all, orig_one


def _agg_queries(calls):
    """The heavy, viewer-independent per-page aggregate (old GROUP BY or base)."""
    return [
        q
        for q in calls["all"]
        if "react_reactions r" in q
        and ("GROUP BY r.message_id" in q or "reaction_aggregate_base" in q)
    ]


def _count_queries_for(calls):
    return [
        q
        for q in calls["one"] + calls["all"]
        if "SELECT COUNT(*) as count FROM react_reactions" in q
    ]


@pytest.fixture
def seeded_messages(messaging_manager, reaction_manager, three_users):
    """A group with three participants and a message carrying two reactions."""
    owner, second, third = three_users
    group = messaging_manager.create_group(
        owner.id, f"l0-cache-{owner.id}", [second.id, third.id]
    )
    msg = messaging_manager.send_message(owner.id, group.id, "cache me")
    reaction_manager.add_reaction(second.id, msg.id, "👍")
    reaction_manager.add_reaction(third.id, msg.id, "❤️")
    reaction_manager._reaction_read_cache.clear()
    return owner, second, third, msg


# --- parity + query reduction ------------------------------------------------


def test_batch_warm_hit_is_parity_and_reduces_queries(
    db, reaction_manager, seeded_messages
):
    owner, second, third, msg = seeded_messages

    with _count_queries(db) as cold_calls:
        cold = reaction_manager.get_reactions_batch(owner.id, [msg.id])
    with _count_queries(db) as warm_calls:
        warm = reaction_manager.get_reactions_batch(owner.id, [msg.id])

    assert warm == cold
    assert len(cold[msg.id]) == 2
    # The viewer-independent aggregate is loaded once on a cold page and never
    # recomputed on the warm path.  Viewer-specific 'me'/block layering needs no
    # aggregate query of its own.
    assert len(_agg_queries(cold_calls)) == 1
    assert _agg_queries(warm_calls) == []

    # The shared base has no access predicate, so the cheap LIVE gate runs on
    # EVERY call (cold and warm), not just on cache hits.  Gate before cache:
    # a revoked viewer cannot read the memoized body.
    def _is_gate(q):
        return q.lstrip().startswith("SELECT m.id FROM msg_messages m")

    gate_cold = [q for q in cold_calls["all"] if _is_gate(q)]
    gate_warm = [q for q in warm_calls["all"] if _is_gate(q)]
    assert len(gate_cold) == 1
    assert len(gate_warm) == 1


def test_count_helpers_cache_without_losing_parity(
    db, reaction_manager, seeded_messages
):
    owner, second, third, msg = seeded_messages

    with _count_queries(db) as cold_calls:
        total_cold = reaction_manager.get_reaction_count(msg.id, viewer_id=owner.id)
        by_emoji_cold = reaction_manager.get_reaction_count_by_emoji(
            msg.id, "👍", viewer_id=owner.id
        )
    with _count_queries(db) as warm_calls:
        total_warm = reaction_manager.get_reaction_count(msg.id, viewer_id=owner.id)
        by_emoji_warm = reaction_manager.get_reaction_count_by_emoji(
            msg.id, "👍", viewer_id=owner.id
        )

    assert (total_warm, by_emoji_warm) == (total_cold, by_emoji_cold) == (2, 1)
    assert len(_count_queries_for(cold_calls)) == 2
    assert _count_queries_for(warm_calls) == []


def test_reactor_list_is_cached_and_identical(db, reaction_manager, seeded_messages):
    owner, second, third, msg = seeded_messages

    with _count_queries(db) as cold_calls:
        cold = reaction_manager.get_reaction_users(owner.id, msg.id, "👍")
    with _count_queries(db) as warm_calls:
        warm = reaction_manager.get_reaction_users(owner.id, msg.id, "👍")

    assert [u.user_id for u in warm] == [u.user_id for u in cold] == [second.id]
    reactor_cold = [q for q in cold_calls["all"] if "react_reactions" in q]
    reactor_warm = [q for q in warm_calls["all"] if "react_reactions" in q]
    assert len(reactor_cold) == 1
    assert reactor_warm == []


# --- no cross-user / cross-credential leak ----------------------------------


def test_blocked_user_filter_is_partitioned_per_viewer(
    reaction_manager, seeded_messages, rel_manager
):
    owner, second, third, msg = seeded_messages
    # Block BEFORE the first read: relationship changes are not reaction writes.
    rel_manager.block_user(owner.id, second.id)

    owner_view = reaction_manager.get_reactions_batch(owner.id, [msg.id])
    third_view = reaction_manager.get_reactions_batch(third.id, [msg.id])

    # The blocked reactor is hidden from the owner but visible to third.
    assert [p["emoji"] for p in owner_view[msg.id]] == ["❤️"]
    assert [p["emoji"] for p in third_view[msg.id]] == ["👍", "❤️"]
    assert reaction_manager.get_reaction_count(msg.id, viewer_id=owner.id) == 1
    assert reaction_manager.get_reaction_count(msg.id, viewer_id=third.id) == 2

    # Repeats hit each viewer's own entry; neither crosses over.
    assert reaction_manager.get_reactions_batch(owner.id, [msg.id]) == owner_view
    assert reaction_manager.get_reactions_batch(third.id, [msg.id]) == third_view
    assert reaction_manager.get_reaction_count(msg.id, viewer_id=owner.id) == 1
    assert reaction_manager.get_reaction_count(msg.id, viewer_id=third.id) == 2


def test_credential_discriminator_partitions_the_same_viewer(
    db, reaction_manager, seeded_messages
):
    owner, second, third, msg = seeded_messages

    user_key = reaction_read_key(
        "reaction_count", owner.id, msg.id, credential=f"user:{owner.id}"
    )
    bot_key = reaction_read_key(
        "reaction_count", owner.id, msg.id, credential=f"bot:999:{owner.id}"
    )
    assert user_key != bot_key

    with _count_queries(db) as first:
        a = reaction_manager.get_reaction_count(
            msg.id, viewer_id=owner.id, credential=f"user:{owner.id}"
        )
    with _count_queries(db) as second_call:
        b = reaction_manager.get_reaction_count(
            msg.id, viewer_id=owner.id, credential=f"bot:999:{owner.id}"
        )
    assert a == b == 2
    # Same viewer but different credentials -> two separate cold queries.
    assert len(_count_queries_for(first)) == 1
    assert len(_count_queries_for(second_call)) == 1


# --- invalidation on every mutation -----------------------------------------


def test_every_reaction_mutation_invalidates(db, reaction_manager, seeded_messages):
    owner, second, third, msg = seeded_messages

    def count():
        return reaction_manager.get_reaction_count(msg.id, viewer_id=owner.id)

    assert count() == 2  # warm

    reaction_manager.add_reaction(owner.id, msg.id, "🎉")
    assert count() == 3  # add

    reaction_manager.remove_reaction(owner.id, msg.id, "🎉")
    assert count() == 2  # remove own

    reaction_manager.add_reaction(second.id, msg.id, "🚀")
    reaction_manager.remove_all_reactions_for_emoji(owner.id, msg.id, "🚀")
    assert count() == 2  # moderator wipe of one emoji

    reaction_manager.add_reaction(third.id, msg.id, "🔥")
    reaction_manager.remove_all_reactions(owner.id, msg.id)
    assert count() == 0  # moderator wipe of all emojis

    # Batch cache sees the same post-wipe truth as the count helper.
    assert reaction_manager.get_reactions_batch(owner.id, [msg.id])[msg.id] == []


def test_delete_custom_emoji_invalidates(
    reaction_manager, seeded_messages, test_db, monkeypatch
):
    owner, second, third, msg = seeded_messages
    emoji_id = 9_000_001
    test_db.execute(
        """INSERT INTO react_custom_emoji
           (id, server_id, name, animated, url, size, created_by, available, created_at)
           VALUES (?, 1, 'cacheemoji', 0, '/e.png', 0, ?, 1, 1000)""",
        (emoji_id, owner.id),
    )
    monkeypatch.setattr(reaction_manager._servers, "get_member", lambda *a: object())
    reaction_manager.add_reaction(second.id, msg.id, f"<:cacheemoji:{emoji_id}>")
    assert (
        reaction_manager.get_reaction_count_by_emoji(
            msg.id, f"<:cacheemoji:{emoji_id}>", viewer_id=owner.id
        )
        == 1
    )  # warm

    # The permission check is exercised elsewhere; here we only assert that a
    # successful delete drops the warm aggregation body.
    monkeypatch.setattr(
        reaction_manager, "_require_manage_server", lambda *a, **k: None
    )
    assert reaction_manager.delete_custom_emoji(owner.id, emoji_id) is True

    assert (
        reaction_manager.get_reaction_count_by_emoji(
            msg.id, f"<:cacheemoji:{emoji_id}>", viewer_id=owner.id
        )
        == 0
    )


# --- authz gate stays in front of the cache ---------------------------------


def test_revoked_access_wins_over_warm_body(reaction_manager, seeded_messages, test_db):
    owner, second, third, msg = seeded_messages

    # Warm the batch and reactor-list entries.
    assert reaction_manager.get_reactions_batch(owner.id, [msg.id])[msg.id]
    assert reaction_manager.get_reaction_users(owner.id, msg.id, "👍")

    # Simulate a live permission revocation (not a reaction write, so the L0
    # cache is intentionally still warm).
    test_db.execute(
        "DELETE FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
        (msg.conversation_id, owner.id),
    )

    assert reaction_manager.get_reactions_batch(owner.id, [msg.id])[msg.id] == []
    with pytest.raises(MessageNotFoundError):
        reaction_manager.get_reaction_users(owner.id, msg.id, "👍")


# --- the cache primitive itself is bounded and short-lived ------------------


def test_cache_is_bounded_and_expires():
    now = [100.0]
    cache = ReactionReadCache(ttl_seconds=3.0, max_entries=2, clock=lambda: now[0])

    cache.set(("k", 1), "a")
    cache.set(("k", 2), "b")
    assert cache.get(("k", 1)) == "a"
    cache.set(("k", 3), "c")  # LRU entry (k,2) is evicted
    assert cache.stats()["evictions"] == 1
    assert cache.get(("k", 2)) != "b"  # evicted value is gone

    now[0] += 3.01  # TTL boundary
    # Expired entries are never returned; each is dropped when read.
    assert cache.get(("k", 1)) != "a"
    assert cache.get(("k", 3)) != "c"
    assert cache.stats()["size"] == 0

    cache.set(("k", 1), "fresh")
    cache.clear()
    assert cache.stats()["size"] == 0
