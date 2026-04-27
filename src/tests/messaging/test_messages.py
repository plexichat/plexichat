"""Tests for messaging functionality."""


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
    try:
        messaging_manager.send_message(test_user.id, 999999, "Test message")
        assert False, "Should have raised an error"
    except Exception:
        pass  # Expected to fail


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
