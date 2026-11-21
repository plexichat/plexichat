"""The reaction add path must not issue redundant queries.

Two queries used to repeat work the hot path already does elsewhere:

* an existence probe ``SELECT 1 ... user_id = ? AND emoji = ?`` ran both before
  and inside the insert transaction.  Only the in-transaction probe is
  authoritative (the ``UNIQUE(message_id, user_id, emoji)`` constraint is the
  final backstop), so the pre-transaction copy was a wasted round trip on every
  add.

* the newly inserted row was re-read with ``SELECT * FROM react_reactions WHERE
  id = ?`` purely to build the returned ``Reaction`` even though every field is
  already known at the call site.

A DM add now costs 7 queries instead of 9.  These tests guard the query budget
*and* that the returned object is byte-for-byte what a fresh row read produces
(for both unicode and custom emoji).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.core.reactions.exceptions import ReactionExistsError

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


def _row_for(reaction_manager, reaction_id):
    """Read the persisted reaction directly for an equivalence assertion."""
    row = reaction_manager._db.fetch_one(
        "SELECT * FROM react_reactions WHERE id = ?", (reaction_id,)
    )
    return reaction_manager._row_to_reaction(row)


def test_add_reaction_issues_no_redundant_queries(
    db, reaction_manager, messaging_manager, three_users
):
    first, second, _third = three_users
    dm = messaging_manager.create_dm(first.id, second.id)
    msg = messaging_manager.send_message(first.id, dm.id, "query budget")

    with _capture_queries(db) as queries:
        reaction = reaction_manager.add_reaction(first.id, msg.id, "👍")

    # Exactly ONE duplicate-existence probe (the in-transaction one).
    probes = [
        q
        for q in queries
        if q
        == "select 1 from react_reactions where message_id = ? and user_id = ? and emoji = ?"
    ]
    assert len(probes) == 1, queries

    # No post-insert re-read of the just-written row.
    assert not [
        q for q in queries if q == "select * from react_reactions where id = ?"
    ], queries

    # A DM add completes in 7 queries or fewer.
    assert len(queries) <= 7, queries

    assert reaction.emoji == "👍"


def test_add_reaction_result_matches_persisted_row(
    reaction_manager, messaging_manager, three_users
):
    first, second, _third = three_users
    dm = messaging_manager.create_dm(first.id, second.id)
    msg = messaging_manager.send_message(first.id, dm.id, "row equivalence")
    shortcode = ":thumbs_up:"

    reaction = reaction_manager.add_reaction(first.id, msg.id, shortcode)

    assert reaction == _row_for(reaction_manager, reaction.id)
    assert reaction.is_custom is False
    assert reaction.custom_emoji_id is None


def test_add_custom_reaction_result_matches_persisted_row(
    users_with_server, sample_image
):
    owner, _member, server, _group, msg, _servers, reaction_manager = users_with_server
    emoji = reaction_manager.create_custom_emoji(
        owner.id, server.id, "budget_emoji", sample_image, "image/png"
    )

    reaction = reaction_manager.add_reaction(
        owner.id, msg.id, f"<:budget_emoji:{emoji.id}>"
    )

    assert reaction == _row_for(reaction_manager, reaction.id)
    assert reaction.is_custom is True
    assert reaction.custom_emoji_id == emoji.id


def test_duplicate_add_still_raises_without_pre_transaction_probe(
    reaction_manager, messaging_manager, three_users
):
    first, second, _third = three_users
    dm = messaging_manager.create_dm(first.id, second.id)
    msg = messaging_manager.send_message(first.id, dm.id, "duplicate")

    reaction_manager.add_reaction(first.id, msg.id, "👍")
    with pytest.raises(ReactionExistsError):
        reaction_manager.add_reaction(first.id, msg.id, "👍")
