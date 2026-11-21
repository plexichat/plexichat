"""Webhook execution resolves its channel + thread target in ONE DB query.

``execute_webhook`` used to issue two separate ``srv_channels`` reads per
execution whenever a ``thread_id`` was supplied: one for the webhook's primary
delivery channel and one for the thread. Both select the same columns and run
back-to-back on the execution hot path. They are now batched into a single
``WHERE id IN (...)`` query, halving the channel-resolution round-trips while
keeping the two per-row cross-tenant checks (and their error messages) intact.

The counter below spies on the manager's DB and counts only the specific
resolution query shape (srv_channels selecting both ``server_id`` and
``conversation_id``), so unrelated reads are not included.
"""

import uuid

import pytest

from src.core.webhooks.exceptions import InvalidWebhookContentError


_RESOLUTION_MARKERS = ("srv_channels", "server_id", "conversation_id")


def _spying_db(db, counter):
    """Wrap fetch_one/fetch_all to count channel-resolution queries."""
    original_one = db.fetch_one
    original_all = db.fetch_all

    def count(sql, params=None):
        if all(marker in sql for marker in _RESOLUTION_MARKERS):
            counter["resolution_queries"] += 1
            counter.setdefault("sql", []).append(" ".join(sql.split()))

    def counting_fetch_one(sql, params=None):
        count(sql, params)
        return original_one(sql, params)

    def counting_fetch_all(sql, params=None):
        count(sql, params)
        return original_all(sql, params)

    db.fetch_one = counting_fetch_one
    db.fetch_all = counting_fetch_all
    return original_one, original_all


def test_execute_thread_batches_channel_resolution_into_one_query(webhook_with_token):
    setup = webhook_with_token
    db = setup["db"]
    wh = setup["webhook"]
    # A thread target equal to the webhook's own channel is still a distinct
    # lookup in the old code (two identical queries). Batching dedupes them.
    thread_id = db.fetch_one(
        "SELECT channel_id FROM webhook_webhooks WHERE id = ?", (wh.id,)
    )["channel_id"]

    counter = {"resolution_queries": 0}
    original_one, original_all = _spying_db(db, counter)
    try:
        result = setup["webhooks"].execute_webhook(
            webhook_id=wh.id,
            token=setup["token"],
            content="batched resolution",
            thread_id=thread_id,
            wait=True,
        )
    finally:
        db.fetch_one = original_one
        db.fetch_all = original_all

    assert result is not None
    assert result.content == "batched resolution"
    # Old code: 2 (channel + thread). New code: 1 (single IN query).
    assert counter["resolution_queries"] == 1, counter.get("sql")


def test_execute_without_thread_uses_one_resolution_query(webhook_with_token):
    setup = webhook_with_token
    db = setup["db"]
    wh = setup["webhook"]

    counter = {"resolution_queries": 0}
    original_one, original_all = _spying_db(db, counter)
    try:
        result = setup["webhooks"].execute_webhook(
            webhook_id=wh.id,
            token=setup["token"],
            content="no thread",
            wait=True,
        )
    finally:
        db.fetch_one = original_one
        db.fetch_all = original_all

    assert result is not None
    assert counter["resolution_queries"] == 1


def test_batched_resolution_still_rejects_foreign_thread(webhook_with_token):
    """The batched query must preserve the cross-tenant thread check."""
    setup = webhook_with_token

    other_owner = setup["auth"].register(
        username=f"foreign_owner_{uuid.uuid4().hex[:8]}",
        email=f"foreign_owner_{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass123!",
    )
    foreign_server = setup["servers"].create_server(other_owner.id, "Foreign Guild")
    foreign_channels = setup["servers"].get_channels(other_owner.id, foreign_server.id)
    foreign_channel_id = foreign_channels[0].id

    # Both channels are fetched in one IN query now; the foreign thread row
    # must still be rejected with the same error as before batching.
    with pytest.raises(InvalidWebhookContentError):
        setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="cross-guild delivery",
            thread_id=foreign_channel_id,
            wait=True,
        )
