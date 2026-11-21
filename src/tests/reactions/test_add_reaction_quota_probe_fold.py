"""Reaction add must probe the three quota gates in one grouped query.

The per-message distinct-emoji check, the "is this emoji already on the
message" flag and the per-user distinct-emoji count all read the same
``message_id`` slice of ``react_reactions``.  They used to be three separate
queries on every add.  A single conditional-aggregate pass answers all three
without changing the message-cap / per-user-cap semantics (including the rule
that the per-user cap is enforced even when the emoji is already on the
message).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.core.reactions.exceptions import ReactionLimitError

pytestmark = [pytest.mark.unit]


@contextmanager
def _capture_queries(db):
    queries = []
    original = db.execute

    def counted(query, params=None):
        queries.append(" ".join((query or "").lower().split()))
        return original(query, params)

    db.execute = counted
    try:
        yield queries
    finally:
        db.execute = original


def _dm_with_message(messaging_manager, three_users):
    first, second, _third = three_users
    dm = messaging_manager.create_dm(first.id, second.id)
    return first, second, messaging_manager.send_message(first.id, dm.id, "quota fold")


def test_add_reaction_uses_one_grouped_quota_probe(
    db, reaction_manager, messaging_manager, three_users
):
    first, second, msg = _dm_with_message(messaging_manager, three_users)

    with _capture_queries(db) as queries:
        reaction_manager.add_reaction(first.id, msg.id, "👍")

    grouped = [
        q
        for q in queries
        if "count(distinct emoji)" in q
        and "max(case when emoji" in q
        and "count(distinct case when user_id" in q
    ]
    assert len(grouped) == 1, queries

    # None of the three standalone probes may remain on the add path.
    assert not [
        q
        for q in queries
        if q == "select 1 from react_reactions where message_id = ? and emoji = ?"
    ], queries
    assert not [
        q
        for q in queries
        if "count(distinct emoji)" in q and "user_id = ?" in q and "max(case" not in q
    ], queries


def test_message_cap_still_allows_an_already_present_emoji(
    reaction_manager, messaging_manager, three_users, monkeypatch
):
    first, second, msg = _dm_with_message(messaging_manager, three_users)
    monkeypatch.setitem(reaction_manager._config, "max_reactions_per_message", 1)

    reaction_manager.add_reaction(first.id, msg.id, "👍")
    reaction = reaction_manager.add_reaction(second.id, msg.id, "👍")

    assert reaction.emoji == "👍"


def test_message_cap_still_blocks_a_new_emoji(
    reaction_manager, messaging_manager, three_users, monkeypatch
):
    first, second, msg = _dm_with_message(messaging_manager, three_users)
    monkeypatch.setitem(reaction_manager._config, "max_reactions_per_message", 1)

    reaction_manager.add_reaction(first.id, msg.id, "👍")
    with pytest.raises(ReactionLimitError):
        reaction_manager.add_reaction(second.id, msg.id, "❤️")


def test_per_user_cap_enforced_even_when_emoji_already_on_message(
    reaction_manager, messaging_manager, three_users, monkeypatch
):
    first, second, msg = _dm_with_message(messaging_manager, three_users)
    monkeypatch.setitem(reaction_manager._config, "max_reactions_per_message", 50)
    monkeypatch.setitem(reaction_manager._config, "max_unique_reactions_per_user", 1)

    reaction_manager.add_reaction(first.id, msg.id, "👍")  # first is at its cap
    reaction_manager.add_reaction(second.id, msg.id, "❤️")  # emoji now on message

    with pytest.raises(ReactionLimitError):
        reaction_manager.add_reaction(first.id, msg.id, "❤️")
