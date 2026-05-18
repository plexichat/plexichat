"""Tests for messaging attachment handling."""

import pytest


@pytest.mark.messaging
class TestAttachments:
    """Tests for message attachments."""

    def test_send_message_with_attachment(self, messaging_manager, two_users):
        """Test sending a message with an attachment."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        attachments = [
            {
                "filename": "test.txt",
                "content_type": "text/plain",
                "size": 100,
                "url": "/media/test.txt",
            }
        ]
        msg = messaging_manager.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="File attached",
            attachments=attachments,
        )
        assert msg is not None
        assert msg.content == "File attached"

    def test_send_message_with_multiple_attachments(self, messaging_manager, two_users):
        """Test sending a message with multiple attachments."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        attachments = [
            {
                "filename": "file1.txt",
                "content_type": "text/plain",
                "size": 100,
                "url": "/media/file1.txt",
            },
            {
                "filename": "file2.png",
                "content_type": "image/png",
                "size": 5000,
                "url": "/media/file2.png",
            },
        ]
        msg = messaging_manager.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="Multiple files",
            attachments=attachments,
        )
        assert msg is not None

    def test_add_attachment_to_message(self, messaging_manager, two_users):
        """Test adding an attachment to an existing message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Original")

        attachment = messaging_manager.add_attachment(
            user_id=user1.id,
            message_id=msg.id,
            filename="added.txt",
            content_type="text/plain",
            size=200,
            url="/media/added.txt",
        )
        assert attachment is not None
        assert attachment.filename == "added.txt"

    def test_get_attachments_for_message(self, messaging_manager, two_users):
        """Test getting attachments for a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        attachments = [
            {
                "filename": "doc.pdf",
                "content_type": "application/pdf",
                "size": 1024,
                "url": "/media/doc.pdf",
            },
        ]
        msg = messaging_manager.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="PDF attached",
            attachments=attachments,
        )

        result = messaging_manager.get_attachments(user1.id, msg.id)
        assert isinstance(result, list)

    def test_delete_attachment(self, messaging_manager, two_users):
        """Test deleting an attachment from a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "With attachment")

        attachment = messaging_manager.add_attachment(
            user_id=user1.id,
            message_id=msg.id,
            filename="delete_me.txt",
            content_type="text/plain",
            size=50,
            url="/media/delete_me.txt",
        )
        result = messaging_manager.delete_attachment(user1.id, attachment.id)
        assert result is True
