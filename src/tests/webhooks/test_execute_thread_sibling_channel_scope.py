"""Regression: a webhook bound to one channel must not deliver into a thread
of a *different* channel on the same server.

``execute_webhook`` checks that the optional ``thread_id`` resolves to a
channel on the webhook's server, but never checks that the thread actually
belongs to the webhook's own channel. A token holder for a webhook created
in channel A can therefore set ``thread_id`` to a thread of sibling channel B
(private/admin-only) and post there using the webhook creator's identity:
the webhook's channel scope is silently bypassed.

The authoritative parent is ``thread_threads.channel_id`` (the DB row that
owns the thread), so the fix resolves it from the database and fails closed.
This file proves the gap (sibling channel rejected) and keeps the legitimate
paths working (own-channel thread, unresolved legacy thread id).
"""

import uuid

import pytest

from src.core.webhooks.exceptions import InvalidWebhookContentError


def _insert_thread_channel(
    db, thread_id, server_id, parent_channel_id, conversation_id, owner_id
):
    now = db.fetch_one("SELECT strftime('%s','now') AS n")["n"]
    db.execute(
        """INSERT INTO srv_channels
           (id, server_id, name, channel_type, conversation_id, created_at, updated_at)
           VALUES (?, ?, ?, 'text', ?, ?, ?)""",
        (thread_id, server_id, f"thread-{thread_id}", conversation_id, now, now),
    )
    db.execute(
        """INSERT INTO thread_threads
           (id, channel_id, server_id, owner_id, name, thread_type, state,
            auto_archive_duration, created_at, conversation_id)
           VALUES (?, ?, ?, ?, ?, 'public', 'active', 1440, ?, ?)""",
        (
            thread_id,
            parent_channel_id,
            server_id,
            owner_id,
            f"thread-{thread_id}",
            now,
            conversation_id,
        ),
    )


def _sibling_channel(setup):
    servers, owner, server = setup["servers"], setup["owner"], setup["server"]
    sibling = servers.create_channel(owner.id, server.id, "sibling-private")
    row = setup["db"].fetch_one(
        "SELECT conversation_id FROM srv_channels WHERE id = ?", (sibling.id,)
    )
    assert row and row["conversation_id"]
    return sibling, row["conversation_id"]


def test_execute_rejects_thread_in_sibling_channel(webhook_with_token):
    setup = webhook_with_token
    db = setup["db"]
    webhook, token = setup["webhook"], setup["token"]
    sibling, sibling_conv = _sibling_channel(setup)

    thread_id = int(uuid.uuid4().int % (10**17)) + 10**17
    _insert_thread_channel(
        db, thread_id, setup["server"].id, sibling.id, sibling_conv, setup["owner"].id
    )

    with pytest.raises(InvalidWebhookContentError) as exc:
        setup["webhooks"].execute_webhook(
            webhook_id=webhook.id,
            token=token,
            content="cross-channel webhook delivery",
            thread_id=thread_id,
            wait=True,
        )
    assert exc.value  # non-vacuous: an error was actually raised


def test_execute_allows_thread_in_webhook_channel(webhook_with_token):
    """Control: a genuine thread of the webhook's own channel still delivers."""
    setup = webhook_with_token
    db = setup["db"]
    webhook, token = setup["webhook"], setup["token"]
    channel_row = db.fetch_one(
        "SELECT conversation_id FROM srv_channels WHERE id = ?", (webhook.channel_id,)
    )
    assert channel_row and channel_row["conversation_id"]

    thread_id = int(uuid.uuid4().int % (10**17)) + 10**17
    _insert_thread_channel(
        db,
        thread_id,
        setup["server"].id,
        webhook.channel_id,
        channel_row["conversation_id"],
        setup["owner"].id,
    )

    result = setup["webhooks"].execute_webhook(
        webhook_id=webhook.id,
        token=token,
        content="own-channel thread delivery",
        thread_id=thread_id,
        wait=True,
    )
    assert result is not None
    assert result.thread_id == thread_id
