"""Tests for messaging functionality."""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError


def test_send_message(messaging_manager, two_users):
    """Test sending a message between users."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    message = messaging_manager.send_message(user1.id, dm.id, "Hello, world!")

    assert message is not None
    assert message.content == "Hello, world!"
    assert message.author_id == user1.id
    assert message.conversation_id == dm.id


def test_send_message_to_nonexistent_conversation(messaging_manager, test_user):
    """Test that sending to a non-existent conversation fails."""
    with pytest.raises(ConversationAccessDeniedError):
        messaging_manager.send_message(test_user.id, 999999, "Test message")


def test_edit_message(messaging_manager, two_users):
    """Test editing a message."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    message = messaging_manager.send_message(user1.id, dm.id, "Original message")

    # Edit the message
    edited = messaging_manager.edit_message(user1.id, message.id, "Edited message")

    assert edited is not None
    assert edited.content == "Edited message"


def test_delete_message(messaging_manager, two_users):
    """Test deleting a message."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    message = messaging_manager.send_message(user1.id, dm.id, "Message to delete")

    # Delete the message
    result = messaging_manager.delete_message(user1.id, message.id)

    assert result is True


def test_listing_after_a_send_returns_models_from_the_recent_cache(
    messaging_manager, two_users, monkeypatch
):
    """A page served from the recent-messages cache must be Message models.

    Sending pushes the new id onto ``msg:recent:{conversation}`` and stores the
    payload under ``msg:obj:{id}``, so the very next listing resolves entirely
    from the cache. The payload round-trips through JSON, so the returned items
    are only usable when the cached type reconstructs back into a model; a
    plain dict makes the manager's status enrichment read ``message.id`` off a
    dict and raise AttributeError, which the channel listing route reports as
    an EMPTY channel - a sent message that vanishes on reload.
    """
    import json

    from src.core.database.cache import serialization
    from src.core.messaging.services.message import retrieval

    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    sent = messaging_manager.send_message(user1.id, dm.id, "cached payload")

    # Exactly what the cache tier stores (and hands back on a hit).
    payload = json.loads(json.dumps(serialization.ensure_serializable(sent)))

    class _FakeClient:
        def __init__(self, ids):
            self._ids = [str(i) for i in ids]

        def lrange(self, key, start, end):
            return self._ids

        def llen(self, key):
            return len(self._ids)

    monkeypatch.setattr(retrieval, "cache_get", lambda key: payload)
    monkeypatch.setattr(
        "src.core.database.get_valkey_client", lambda: _FakeClient([sent.id])
    )

    messages = messaging_manager.get_messages(user1.id, dm.id, 50)

    assert [m.id for m in messages] == [sent.id]
    assert messages[0].content == "cached payload"
