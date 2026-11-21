"""Reactor-list channel binding must not re-fetch the message row.

The reactor-list route already fetched the message in its live access gate, and
the core manager fetches it again before its own gate. Resolving the URL channel
used to add a third message-row query plus a channel query. Fold those into one
LEFT JOIN so the message-in-channel check costs a single round trip with
identical output for DMs, server channels and missing messages.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

pytestmark = [pytest.mark.unit]


@contextmanager
def _count_reads(db):
    calls = []
    orig_one, orig_all = db.fetch_one, db.fetch_all

    def counted_one(query, params=None):
        calls.append(" ".join((query or "").split()))
        return orig_one(query, params)

    def counted_all(query, params=None):
        calls.append(" ".join((query or "").split()))
        return orig_all(query, params)

    db.fetch_one = counted_one
    db.fetch_all = counted_all
    try:
        yield calls
    finally:
        db.fetch_one, db.fetch_all = orig_one, orig_all


def _resolve(db, reactions, message_id):
    with _count_reads(db) as calls:
        result = reactions.resolve_message_channel(message_id)
    return result, calls


def test_dm_resolution_is_one_query_and_keeps_conversation_fallback(
    db, reaction_manager, messaging_manager, three_users
):
    owner, second, third = three_users
    group = messaging_manager.create_group(
        owner.id, "channel-resolution-dm", [second.id, third.id]
    )
    msg = messaging_manager.send_message(owner.id, group.id, "resolve me")

    result, calls = _resolve(db, reaction_manager, msg.id)

    assert len(calls) == 1, calls
    assert result == {
        "conversation_id": msg.conversation_id,
        "channel_id": msg.conversation_id,
        "is_server_channel": False,
    }


def test_server_channel_resolution_is_one_query_and_returns_real_channel_id(
    db, reaction_manager, messaging_manager, server_manager, three_users
):
    owner, second, third = three_users
    server = server_manager.create_server(owner.id, f"channel-resolution-{owner.id}")
    channel = server_manager.create_channel(owner.id, server.id, "general", "text")
    msg = messaging_manager.send_message(
        owner.id, channel.conversation_id, "resolve me"
    )

    result, calls = _resolve(db, reaction_manager, msg.id)

    assert len(calls) == 1, calls
    assert result == {
        "conversation_id": channel.conversation_id,
        "channel_id": channel.id,
        "is_server_channel": True,
    }


def test_missing_message_still_resolves_to_none_in_one_query(db, reaction_manager):
    result, calls = _resolve(db, reaction_manager, 9_100_002)

    assert len(calls) == 1, calls
    assert result is None
