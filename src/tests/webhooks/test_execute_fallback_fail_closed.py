"""Webhook execution must fail closed when the messaging send is rejected.

A webhook holding a valid token used to swallow *every* exception raised by
``messaging.send_message`` and insert a raw row directly into ``msg_messages``.
That fallback failed open on authorization and content-validation failures:

* ``ConversationAccessDeniedError`` (creator no longer a participant / member
  of the target conversation) was swallowed and the message was still written,
  letting the webhook post into a conversation its creator was not authorized
  to write;
* ``InvalidContentError`` (content rejected by the normal send path's XSS /
  SQLi blocklist) was swallowed and the unfiltered content was persisted.

The fallback is removed: a rejected send propagates and no message row is
created. These tests pin both gates with a full webhook manager whose messaging
module is wired (the shared ``webhook_manager`` fixture intentionally passes
``messaging_module=None``, so it never exercised this path).
"""

import uuid

import pytest

from src.core.messaging.exceptions import (
    ConversationAccessDeniedError,
    InvalidContentError,
)
from src.core.webhooks.manager import WebhookManager


def _make_webhook(db, auth_manager, messaging_manager, server_manager):
    manager = WebhookManager(
        db,
        auth_module=auth_manager,
        messaging_module=messaging_manager,
        servers_module=server_manager,
    )
    uid = uuid.uuid4().hex[:8]
    owner = auth_manager.register(
        username=f"owner_{uid}",
        email=f"owner_{uid}@example.com",
        password="TestPass123!",
    )
    server = server_manager.create_server(owner.id, f"Fallback Test {uid}")
    channel = server_manager.get_channels(owner.id, server.id)[0]
    webhook = manager.create_webhook(
        user_id=owner.id, channel_id=channel.id, name=f"fallback-{uid}"
    )
    return manager, webhook, owner, channel


def _message_count(db) -> int:
    return int(db.fetch_one("SELECT COUNT(*) AS n FROM msg_messages")["n"])


def test_denied_send_does_not_post_message(
    db, auth_manager, messaging_manager, server_manager, monkeypatch
):
    """A send denied for the creator must not fall back to a raw insert."""
    manager, webhook, owner, channel = _make_webhook(
        db, auth_manager, messaging_manager, server_manager
    )

    # Baseline: the creator can post through the webhook while authorized.
    baseline = _message_count(db)
    manager.execute_webhook(
        webhook_id=webhook.id, token=webhook.token, content="baseline", wait=True
    )
    assert _message_count(db) == baseline + 1

    # Simulate the creator losing authorization for the conversation (e.g.
    # kicked from the server and stripped of conversation participants).
    def _deny(*args, **kwargs):
        raise ConversationAccessDeniedError("Not a participant in this conversation")

    monkeypatch.setattr(messaging_manager, "send_message", _deny)

    before = _message_count(db)
    with pytest.raises(ConversationAccessDeniedError):
        manager.execute_webhook(
            webhook_id=webhook.id,
            token=webhook.token,
            content="must not be posted",
            wait=True,
        )
    assert _message_count(db) == before, (
        "webhook must not create a message when the send is denied"
    )


def test_rejected_content_does_not_bypass_send_filter(
    db, auth_manager, messaging_manager, server_manager
):
    """Content the normal send path rejects must not be persisted via webhook."""
    manager, webhook, owner, channel = _make_webhook(
        db, auth_manager, messaging_manager, server_manager
    )

    payload = "<script>alert(1)</script> " + uuid.uuid4().hex[:8]
    # The content filter rejects this pattern on the ordinary send path.
    with pytest.raises(InvalidContentError):
        messaging_manager.send_message(
            user_id=owner.id, conversation_id=channel.id, content=payload
        )

    before = _message_count(db)
    with pytest.raises(InvalidContentError):
        manager.execute_webhook(
            webhook_id=webhook.id, token=webhook.token, content=payload, wait=True
        )
    assert _message_count(db) == before, (
        "webhook must not persist content rejected by the send filter"
    )
