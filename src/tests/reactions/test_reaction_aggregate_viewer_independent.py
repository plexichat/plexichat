"""The reaction aggregate for a message page is viewer-independent.

``get_reactions_batch`` used to fold the viewer's ``me`` flag and two-way
block filter into one ``GROUP BY`` and memoize the whole body under a key that
included the viewer id.  A busy channel read by many users therefore recomputed
the exact same count/emoji aggregate once per viewer.  The reactor identities
carried by that query are enough to derive both the ``me`` bit and the
block-filtered count per viewer, so the grouped aggregate only needs to be
computed once per page.

These tests pin the query-count contract and the per-viewer correctness that
must survive the split.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

pytestmark = [pytest.mark.unit]


@contextmanager
def _count_queries(db):
    calls: dict[str, list[str]] = {"all": [], "one": []}
    orig_all, orig_one = db.fetch_all, db.fetch_one

    def counted_all(query, params=None):
        calls["all"].append((query or "").strip())
        return orig_all(query, params)

    def counted_one(query, params=None):
        calls["one"].append((query or "").strip())
        return orig_one(query, params)

    db.fetch_all = counted_all
    db.fetch_one = counted_one
    try:
        yield calls
    finally:
        db.fetch_all, db.fetch_one = orig_all, orig_one


def _aggregate_queries(calls):
    """Heavy per-page aggregate: the old viewer-keyed GROUP BY or the new base."""
    return [
        q
        for q in calls["all"]
        if "react_reactions" in q
        and ("GROUP BY r.message_id" in q or "reaction_aggregate_base" in q)
    ]


def test_aggregate_computed_once_across_viewers(
    db, reaction_manager, messaging_manager, three_users
):
    owner, second, third = three_users
    group = messaging_manager.create_group(
        owner.id, f"agg-share-{owner.id}", [second.id, third.id]
    )
    msg = messaging_manager.send_message(owner.id, group.id, "shared aggregate")
    reaction_manager.add_reaction(second.id, msg.id, "👍")
    reaction_manager.add_reaction(third.id, msg.id, "❤️")
    reaction_manager._reaction_read_cache.clear()

    with _count_queries(db) as calls:
        owner_view = reaction_manager.get_reactions_batch(owner.id, [msg.id])
        third_view = reaction_manager.get_reactions_batch(third.id, [msg.id])

    assert [p["emoji"] for p in owner_view[msg.id]] == ["👍", "❤️"]
    assert [p["emoji"] for p in third_view[msg.id]] == ["👍", "❤️"]
    assert [p["count"] for p in owner_view[msg.id]] == [1, 1]
    # The 'me' bit is the only viewer-specific part of the body.
    assert all(p["me"] is False for p in owner_view[msg.id])
    assert [p["me"] for p in third_view[msg.id]] == [False, True]

    heavy = _aggregate_queries(calls)
    assert len(heavy) == 1, (
        f"reaction aggregate recomputed for each viewer: {len(heavy)} queries "
        f"for 2 viewers\n{heavy}"
    )


def test_shared_aggregate_preserves_two_way_block_filter(
    db, reaction_manager, messaging_manager, rel_manager, three_users
):
    owner, second, third = three_users
    group = messaging_manager.create_group(
        owner.id, f"agg-block-{owner.id}", [second.id, third.id]
    )
    msg = messaging_manager.send_message(owner.id, group.id, "blocked aggregate")
    reaction_manager.add_reaction(second.id, msg.id, "👍")
    reaction_manager.add_reaction(third.id, msg.id, "❤️")
    # Block BEFORE the first read so the shared base is built fresh.
    rel_manager.block_user(owner.id, second.id)
    reaction_manager._reaction_read_cache.clear()

    owner_view = reaction_manager.get_reactions_batch(owner.id, [msg.id])
    third_view = reaction_manager.get_reactions_batch(third.id, [msg.id])

    # Owner must not see the blocked reactor's emoji, and its count shrinks.
    assert [p["emoji"] for p in owner_view[msg.id]] == ["❤️"]
    assert [p["count"] for p in owner_view[msg.id]] == [1]
    assert all(p["me"] is False for p in owner_view[msg.id])
    # A different viewer still sees the full, unfiltered aggregate.
    assert [p["emoji"] for p in third_view[msg.id]] == ["👍", "❤️"]
    assert [p["me"] for p in third_view[msg.id]] == [False, True]


def test_shared_aggregate_honours_reverse_block_direction(
    db, reaction_manager, messaging_manager, rel_manager, three_users
):
    """The 'blocked by reactor' half of the two-way filter survives the split."""
    owner, second, third = three_users
    group = messaging_manager.create_group(
        owner.id, f"agg-revblock-{owner.id}", [second.id, third.id]
    )
    msg = messaging_manager.send_message(owner.id, group.id, "reverse block")
    reaction_manager.add_reaction(second.id, msg.id, "👍")
    reaction_manager.add_reaction(third.id, msg.id, "❤️")
    # second blocked the viewer -- the viewer must not see second's reaction.
    rel_manager.block_user(second.id, owner.id)
    reaction_manager._reaction_read_cache.clear()

    owner_view = reaction_manager.get_reactions_batch(owner.id, [msg.id])
    assert [p["emoji"] for p in owner_view[msg.id]] == ["❤️"]
    assert [p["count"] for p in owner_view[msg.id]] == [1]


def test_malformed_viewer_id_is_denied(
    db, reaction_manager, messaging_manager, three_users
):
    owner, second, third = three_users
    group = messaging_manager.create_group(
        owner.id, f"agg-viewer-{owner.id}", [second.id, third.id]
    )
    msg = messaging_manager.send_message(owner.id, group.id, "viewer id")
    reaction_manager.add_reaction(second.id, msg.id, "👍")
    reaction_manager._reaction_read_cache.clear()

    # bool/float/string ids are never laundered into a real viewer identity.
    assert reaction_manager.get_reactions_batch(True, [msg.id]) == {msg.id: []}
    assert reaction_manager.get_reactions_batch(float(owner.id), [msg.id]) == {
        msg.id: []
    }
    assert reaction_manager.get_reactions_batch(str(owner.id), [msg.id]) == {msg.id: []}
    # A genuine strict int still sees the real aggregate.
    assert [
        p["emoji"]
        for p in reaction_manager.get_reactions_batch(owner.id, [msg.id])[msg.id]
    ] == ["👍"]
