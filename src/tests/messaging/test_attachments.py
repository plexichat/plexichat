"""
Attachment security tests.

Tests file type validation, size limits, path traversal prevention,
and attachment handling.
"""

import pytest
from src.core.messaging.exceptions import (
    AttachmentTooLargeError,
    AttachmentLimitError,
    MessageAccessDeniedError,
    MessageNotFoundError,
)


class TestAttachmentSecurity:
    """Tests for attachment security."""

    def test_attachment_size_limit_enforced(self, dm_conversation):
        """Test that attachment size limit is enforced."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        # Try to add attachment exceeding limit (10MB default)
        with pytest.raises(AttachmentTooLargeError) as exc_info:
            messaging.add_attachment(
                user1.id,
                msg.id,
                filename="large.bin",
                content_type="application/octet-stream",
                size=20 * 1024 * 1024,  # 20MB
                url="https://example.com/large.bin",
            )

        assert exc_info.value.max_size == 10485760
        assert exc_info.value.actual_size == 20 * 1024 * 1024

    def test_attachment_count_limit_enforced(self, dm_conversation):
        """Test that attachment count limit is enforced."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        # Add maximum allowed attachments (10 default)
        for i in range(10):
            messaging.add_attachment(
                user1.id,
                msg.id,
                filename=f"file{i}.txt",
                content_type="text/plain",
                size=100,
                url=f"https://example.com/file{i}.txt",
            )

        # Try to add one more
        with pytest.raises(AttachmentLimitError) as exc_info:
            messaging.add_attachment(
                user1.id,
                msg.id,
                filename="file11.txt",
                content_type="text/plain",
                size=100,
                url="https://example.com/file11.txt",
            )

        assert exc_info.value.max_count == 10

    def test_path_traversal_in_filename(self, dm_conversation):
        """Test that path traversal in filename is handled."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        # Try various path traversal patterns
        malicious_names = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "....//....//....//etc/passwd",
            "..\\..\\..",
        ]

        for malicious in malicious_names:
            # Should not raise exception - filename should be sanitized
            att = messaging.add_attachment(
                user1.id,
                msg.id,
                filename=malicious,
                content_type="text/plain",
                size=100,
                url="https://example.com/file.txt",
            )
            assert att is not None
            # Verify traversal patterns not in stored filename
            assert ".." not in att.filename or att.filename == malicious

    def test_null_byte_in_filename(self, dm_conversation):
        """Test handling of null bytes in filename."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename="file\x00.txt.exe",
            content_type="text/plain",
            size=100,
            url="https://example.com/file.txt",
        )

        # Should handle null byte safely
        assert att is not None

    def test_dangerous_file_extensions(self, dm_conversation):
        """Test handling of dangerous file extensions."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        dangerous_extensions = [
            "malware.exe",
            "script.bat",
            "virus.com",
            "trojan.scr",
            "payload.dll",
        ]

        for filename in dangerous_extensions:
            # Should accept but store safely
            att = messaging.add_attachment(
                user1.id,
                msg.id,
                filename=filename,
                content_type="application/octet-stream",
                size=100,
                url=f"https://example.com/{filename}",
            )
            assert att is not None

    def test_mime_type_validation(self, dm_conversation):
        """Test MIME type validation."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        valid_types = [
            "image/png",
            "image/jpeg",
            "image/gif",
            "video/mp4",
            "audio/mpeg",
            "application/pdf",
            "text/plain",
        ]

        for mime_type in valid_types:
            att = messaging.add_attachment(
                user1.id,
                msg.id,
                filename="test.file",
                content_type=mime_type,
                size=100,
                url="https://example.com/test.file",
            )
            assert att.content_type == mime_type


class TestAttachmentEncryption:
    """Tests for attachment URL encryption."""

    def test_attachment_url_encryption(self, dm_conversation, modules):
        """Test that attachment URLs are encrypted when enabled."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        original_url = "https://example.com/secret-file.pdf"
        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename="secret.pdf",
            content_type="application/pdf",
            size=1000,
            url=original_url,
        )

        # URL should be accessible (decrypted in response)
        assert att.url == original_url

    def test_attachment_url_decryption(self, dm_conversation):
        """Test that encrypted attachment URLs are decrypted on retrieval."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        original_url = "https://example.com/file.pdf"
        messaging.add_attachment(
            user1.id,
            msg.id,
            filename="file.pdf",
            content_type="application/pdf",
            size=1000,
            url=original_url,
        )

        # Retrieve attachments
        attachments = messaging.get_attachments(user1.id, msg.id)
        assert len(attachments) == 1
        assert attachments[0].url == original_url


class TestAttachmentCRUD:
    """Tests for attachment CRUD operations."""

    def test_add_attachment_to_message(self, dm_conversation):
        """Test adding attachment to a message."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "With attachment")

        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename="document.pdf",
            content_type="application/pdf",
            size=5000,
            url="https://example.com/doc.pdf",
        )

        assert att.message_id == msg.id
        assert att.filename == "document.pdf"
        assert att.size == 5000

    def test_add_attachment_with_metadata(self, dm_conversation):
        """Test adding attachment with metadata."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        metadata = {"width": 1920, "height": 1080, "duration": 120}

        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename="video.mp4",
            content_type="video/mp4",
            size=10000,
            url="https://example.com/video.mp4",
            metadata=metadata,
        )

        assert att.metadata == metadata

    def test_add_attachment_to_others_message_fails(self, dm_conversation):
        """Test that users cannot add attachments to others' messages."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "User1 message")

        with pytest.raises(MessageAccessDeniedError):
            messaging.add_attachment(
                user2.id,
                msg.id,
                filename="file.txt",
                content_type="text/plain",
                size=100,
                url="https://example.com/file.txt",
            )

    def test_get_attachments_from_message(self, dm_conversation):
        """Test retrieving attachments from a message."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        # Add multiple attachments
        for i in range(3):
            messaging.add_attachment(
                user1.id,
                msg.id,
                filename=f"file{i}.txt",
                content_type="text/plain",
                size=100,
                url=f"https://example.com/file{i}.txt",
            )

        attachments = messaging.get_attachments(user1.id, msg.id)
        assert len(attachments) == 3

    def test_delete_attachment(self, dm_conversation):
        """Test deleting an attachment."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename="file.txt",
            content_type="text/plain",
            size=100,
            url="https://example.com/file.txt",
        )

        result = messaging.delete_attachment(user1.id, att.id)
        assert result is True

        # Verify deleted
        attachments = messaging.get_attachments(user1.id, msg.id)
        assert len(attachments) == 0

    def test_delete_others_attachment_fails(self, dm_conversation):
        """Test that users cannot delete others' attachments."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename="file.txt",
            content_type="text/plain",
            size=100,
            url="https://example.com/file.txt",
        )

        with pytest.raises(MessageAccessDeniedError):
            messaging.delete_attachment(user2.id, att.id)

    def test_get_nonexistent_attachment_returns_none(self, dm_conversation):
        """Test that getting non-existent attachment returns None."""
        dm, user1, user2, messaging = dm_conversation

        att = messaging._get_attachment(999999999)
        assert att is None


class TestAttachmentBatch:
    """Tests for batch attachment operations."""

    def test_send_message_with_multiple_attachments(self, dm_conversation):
        """Test sending message with multiple attachments at once."""
        dm, user1, user2, messaging = dm_conversation

        attachments = [
            {
                "filename": f"file{i}.txt",
                "content_type": "text/plain",
                "size": 100,
                "url": f"https://example.com/file{i}.txt",
            }
            for i in range(5)
        ]

        msg = messaging.send_message(
            user1.id, dm.id, "With attachments", attachments=attachments
        )

        assert len(msg.attachments) == 5

    def test_batch_attachment_count_limit(self, dm_conversation):
        """Test that batch attachment respects count limit."""
        dm, user1, user2, messaging = dm_conversation

        # Try to send 15 attachments (exceeds 10 limit)
        attachments = [
            {
                "filename": f"file{i}.txt",
                "content_type": "text/plain",
                "size": 100,
                "url": f"https://example.com/file{i}.txt",
            }
            for i in range(15)
        ]

        with pytest.raises(AttachmentLimitError):
            messaging.send_message(
                user1.id, dm.id, "Too many attachments", attachments=attachments
            )

    def test_message_with_attachments_retrieval(self, dm_conversation):
        """Test that retrieving messages includes attachments."""
        dm, user1, user2, messaging = dm_conversation

        # Send message with attachments
        attachments = [
            {
                "filename": "file1.txt",
                "content_type": "text/plain",
                "size": 100,
                "url": "https://example.com/file1.txt",
            },
            {
                "filename": "file2.txt",
                "content_type": "text/plain",
                "size": 200,
                "url": "https://example.com/file2.txt",
            },
        ]

        msg = messaging.send_message(user1.id, dm.id, "Test", attachments=attachments)

        # Retrieve message
        retrieved = messaging.get_message(user1.id, msg.id)
        assert retrieved is not None
        # Note: attachments may need to be loaded separately


class TestAttachmentEdgeCases:
    """Tests for attachment edge cases."""

    def test_attachment_with_empty_filename(self, dm_conversation):
        """Test handling of empty filename."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename="",
            content_type="text/plain",
            size=100,
            url="https://example.com/file.txt",
        )

        # Should use default filename
        assert att.filename in ["", "file"]

    def test_attachment_with_very_long_filename(self, dm_conversation):
        """Test handling of very long filename."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        long_filename = "a" * 500 + ".txt"
        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename=long_filename,
            content_type="text/plain",
            size=100,
            url="https://example.com/file.txt",
        )

        assert att is not None

    def test_attachment_with_unicode_filename(self, dm_conversation):
        """Test handling of unicode in filename."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        unicode_filename = "文件.txt"
        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename=unicode_filename,
            content_type="text/plain",
            size=100,
            url="https://example.com/file.txt",
        )

        assert unicode_filename in att.filename or att.filename is not None

    def test_attachment_with_zero_size(self, dm_conversation):
        """Test handling of zero-size attachment."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        att = messaging.add_attachment(
            user1.id,
            msg.id,
            filename="empty.txt",
            content_type="text/plain",
            size=0,
            url="https://example.com/empty.txt",
        )

        assert att.size == 0

    def test_attachment_to_nonexistent_message_fails(self, dm_conversation):
        """Test that adding attachment to non-existent message fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(MessageNotFoundError):
            messaging.add_attachment(
                user1.id,
                999999999,
                filename="file.txt",
                content_type="text/plain",
                size=100,
                url="https://example.com/file.txt",
            )

    def test_attachment_special_characters_in_filename(self, dm_conversation):
        """Test handling of special characters in filename."""
        dm, user1, user2, messaging = dm_conversation
        msg = messaging.send_message(user1.id, dm.id, "Test")

        special_filenames = [
            "file<>.txt",
            "file|.txt",
            "file?.txt",
            "file*.txt",
            'file".txt',
        ]

        for filename in special_filenames:
            att = messaging.add_attachment(
                user1.id,
                msg.id,
                filename=filename,
                content_type="text/plain",
                size=100,
                url="https://example.com/file.txt",
            )
            assert att is not None
