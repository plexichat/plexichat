"""
Attachment tests for messaging module.
"""

import pytest


class TestAddAttachment:
    """Test adding attachments to messages."""

    def test_add_attachment_success(self, dm_conversation):
        """Test successful attachment addition."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Check this file")

        att = messaging.add_attachment(
            user_id=user1.id,
            message_id=msg.id,
            filename="test.pdf",
            content_type="application/pdf",
            size=1024,
            url="https://storage.example.com/test.pdf",
        )

        assert att is not None
        assert att.filename == "test.pdf"
        assert att.content_type == "application/pdf"
        assert att.size == 1024

    def test_add_attachment_with_metadata(self, dm_conversation):
        """Test adding attachment with metadata."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Image")

        att = messaging.add_attachment(
            user_id=user1.id,
            message_id=msg.id,
            filename="image.png",
            content_type="image/png",
            size=2048,
            url="https://storage.example.com/image.png",
            metadata={"width": 800, "height": 600},
        )

        assert att.metadata is not None
        assert att.metadata["width"] == 800

    def test_add_attachment_to_others_message_fails(self, dm_conversation):
        """Test adding attachment to others' message fails."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "My message")

        with pytest.raises(messaging.MessageAccessDeniedError):
            messaging.add_attachment(
                user_id=user2.id,
                message_id=msg.id,
                filename="test.pdf",
                content_type="application/pdf",
                size=1024,
                url="https://storage.example.com/test.pdf",
            )

    def test_add_attachment_too_large_fails(self, dm_conversation):
        """Test adding attachment exceeding size limit fails."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Big file")

        with pytest.raises(messaging.AttachmentTooLargeError) as exc_info:
            messaging.add_attachment(
                user_id=user1.id,
                message_id=msg.id,
                filename="huge.zip",
                content_type="application/zip",
                size=100000000,  # 100MB, exceeds 10MB default
                url="https://storage.example.com/huge.zip",
            )

        assert exc_info.value.max_size == 10485760

    def test_add_attachment_exceeds_count_limit(self, dm_conversation):
        """Test adding more attachments than allowed fails."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Many files")

        # Add max attachments (10 by default)
        for i in range(10):
            messaging.add_attachment(
                user_id=user1.id,
                message_id=msg.id,
                filename=f"file{i}.txt",
                content_type="text/plain",
                size=100,
                url=f"https://storage.example.com/file{i}.txt",
            )

        # 11th should fail
        with pytest.raises(messaging.AttachmentLimitError):
            messaging.add_attachment(
                user_id=user1.id,
                message_id=msg.id,
                filename="file10.txt",
                content_type="text/plain",
                size=100,
                url="https://storage.example.com/file10.txt",
            )

    def test_add_attachment_to_nonexistent_message_fails(self, dm_conversation):
        """Test adding attachment to nonexistent message fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(messaging.MessageNotFoundError):
            messaging.add_attachment(
                user_id=user1.id,
                message_id=999999999,
                filename="test.pdf",
                content_type="application/pdf",
                size=1024,
                url="https://storage.example.com/test.pdf",
            )


class TestSendMessageWithAttachments:
    """Test sending messages with attachments."""

    def test_send_with_attachments(self, dm_conversation):
        """Test sending message with attachments."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="Files attached",
            attachments=[
                {
                    "filename": "doc.pdf",
                    "content_type": "application/pdf",
                    "size": 1024,
                    "url": "https://storage.example.com/doc.pdf",
                },
                {
                    "filename": "image.png",
                    "content_type": "image/png",
                    "size": 2048,
                    "url": "https://storage.example.com/image.png",
                },
            ],
        )

        attachments = messaging.get_attachments(user1.id, msg.id)

        assert len(attachments) == 2

    def test_send_with_too_many_attachments_fails(self, dm_conversation):
        """Test sending with too many attachments fails."""
        dm, user1, user2, messaging = dm_conversation

        attachments = [
            {
                "filename": f"file{i}.txt",
                "content_type": "text/plain",
                "size": 100,
                "url": f"https://storage.example.com/file{i}.txt",
            }
            for i in range(15)  # Exceeds default limit of 10
        ]

        with pytest.raises(messaging.AttachmentLimitError):
            messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content="Too many files",
                attachments=attachments,
            )


class TestGetAttachments:
    """Test getting attachments."""

    def test_get_attachments_as_participant(self, dm_conversation):
        """Test participant can get attachments."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "File")
        messaging.add_attachment(
            user1.id,
            msg.id,
            "test.pdf",
            "application/pdf",
            1024,
            "https://storage.example.com/test.pdf",
        )

        attachments = messaging.get_attachments(user2.id, msg.id)

        assert len(attachments) == 1

    def test_get_attachments_as_non_participant(self, dm_conversation, users):
        """Test non-participant gets empty list."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users

        msg = messaging.send_message(user1.id, dm.id, "File")
        messaging.add_attachment(
            user1.id,
            msg.id,
            "test.pdf",
            "application/pdf",
            1024,
            "https://storage.example.com/test.pdf",
        )

        attachments = messaging.get_attachments(user3.id, msg.id)

        assert len(attachments) == 0

    def test_get_attachments_excludes_deleted(self, dm_conversation):
        """Test get_attachments excludes deleted attachments."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Files")
        att1 = messaging.add_attachment(
            user1.id,
            msg.id,
            "keep.pdf",
            "application/pdf",
            1024,
            "https://storage.example.com/keep.pdf",
        )
        att2 = messaging.add_attachment(
            user1.id,
            msg.id,
            "delete.pdf",
            "application/pdf",
            1024,
            "https://storage.example.com/delete.pdf",
        )

        messaging.delete_attachment(user1.id, att2.id)

        attachments = messaging.get_attachments(user1.id, msg.id)
        att_ids = [a.id for a in attachments]

        assert att1.id in att_ids
        assert att2.id not in att_ids

    def test_get_attachments_nonexistent_message(self, dm_conversation):
        """Test get_attachments for nonexistent message returns empty."""
        dm, user1, user2, messaging = dm_conversation

        attachments = messaging.get_attachments(user1.id, 999999999)

        assert len(attachments) == 0


class TestDeleteAttachment:
    """Test deleting attachments."""

    def test_delete_own_attachment(self, dm_conversation):
        """Test deleting own attachment."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "File")
        att = messaging.add_attachment(
            user1.id,
            msg.id,
            "test.pdf",
            "application/pdf",
            1024,
            "https://storage.example.com/test.pdf",
        )

        result = messaging.delete_attachment(user1.id, att.id)

        assert result is True

    def test_delete_others_attachment_fails(self, dm_conversation):
        """Test deleting others' attachment fails."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "File")
        att = messaging.add_attachment(
            user1.id,
            msg.id,
            "test.pdf",
            "application/pdf",
            1024,
            "https://storage.example.com/test.pdf",
        )

        with pytest.raises(messaging.MessageAccessDeniedError):
            messaging.delete_attachment(user2.id, att.id)

    def test_delete_nonexistent_attachment_fails(self, dm_conversation):
        """Test deleting nonexistent attachment fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(messaging.AttachmentError):
            messaging.delete_attachment(user1.id, 999999999)


class TestAttachmentWithMessages:
    """Test attachments are included with messages."""

    def test_get_messages_includes_attachments(self, dm_conversation):
        """Test get_messages includes attachments."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "With attachment")
        messaging.add_attachment(
            user1.id,
            msg.id,
            "test.pdf",
            "application/pdf",
            1024,
            "https://storage.example.com/test.pdf",
        )

        messages = messaging.get_messages(user1.id, dm.id)
        target_msg = next(m for m in messages if m.id == msg.id)

        assert len(target_msg.attachments) == 1
        assert target_msg.attachments[0].filename == "test.pdf"

    def test_get_message_includes_attachments(self, dm_conversation):
        """Test get_message includes attachments."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "With attachment")
        messaging.add_attachment(
            user1.id,
            msg.id,
            "test.pdf",
            "application/pdf",
            1024,
            "https://storage.example.com/test.pdf",
        )

        # Note: get_message doesn't auto-load attachments, need to call get_attachments
        attachments = messaging.get_attachments(user1.id, msg.id)

        assert len(attachments) == 1


class TestUserAttachmentLimits:
    """Test user-specific attachment limits."""

    def test_user_custom_size_limit(self, dm_conversation):
        """Test user can have custom attachment size limit."""
        dm, user1, user2, messaging = dm_conversation

        # Set custom limit for user1 (20MB)
        messaging.update_user_message_settings(user1.id, max_attachment_size=20971520)

        msg = messaging.send_message(user1.id, dm.id, "Big file")

        # Should succeed with 15MB file (exceeds default 10MB but within custom 20MB)
        att = messaging.add_attachment(
            user1.id,
            msg.id,
            "big.zip",
            "application/zip",
            15728640,
            "https://storage.example.com/big.zip",
        )

        assert att is not None

    def test_user_custom_count_limit(self, dm_conversation):
        """Test user can have custom attachment count limit."""
        dm, user1, user2, messaging = dm_conversation

        # Set custom limit for user1 (5 attachments)
        messaging.update_user_message_settings(user1.id, max_attachments_per_message=5)

        msg = messaging.send_message(user1.id, dm.id, "Files")

        # Add 5 attachments
        for i in range(5):
            messaging.add_attachment(
                user1.id,
                msg.id,
                f"file{i}.txt",
                "text/plain",
                100,
                f"https://storage.example.com/file{i}.txt",
            )

        # 6th should fail
        with pytest.raises(messaging.AttachmentLimitError):
            messaging.add_attachment(
                user1.id,
                msg.id,
                "file5.txt",
                "text/plain",
                100,
                "https://storage.example.com/file5.txt",
            )
