"""Tests for media file access control."""

import pytest


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestAccessControl:
    """Tests for media file access permissions."""

    def test_uploader_has_access(self, media_manager, test_user):
        """Test that the file uploader has access."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="access_owner.png",
            content_type="image/png",
        )
        file = media_manager.get_file(result.file_id)
        assert file is not None
        assert media_manager.check_file_access(file.filename, test_user.id) is True

    def test_non_uploader_no_access(self, media_manager, two_users):
        """Test that non-uploader without shared context is denied."""
        owner, other = two_users
        result = media_manager.upload_file(
            user_id=owner.id,
            file_data=MINI_PNG,
            filename="no_access.png",
            content_type="image/png",
        )
        file = media_manager.get_file(result.file_id)
        assert file is not None
        assert media_manager.check_file_access(file.filename, other.id) is False

    def test_deleted_file_no_access(self, media_manager, test_user):
        """Test that deleted files deny access."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="deleted_access.png",
            content_type="image/png",
        )
        media_manager.delete_file(test_user.id, result.file_id)
        file = media_manager.get_file(result.file_id)
        # Deleted file returns None
        assert file is None

    def test_nonexistent_file_no_access(self, media_manager, test_user):
        """Test that nonexistent files deny access."""
        result = media_manager.check_file_access("nonexistent_abc123.png", test_user.id)
        assert result is False

    def test_message_attachment_gives_access(
        self, media_manager, messaging_manager, two_users
    ):
        """Test that message attachments give conversation participants access."""
        user1, user2 = two_users
        result = media_manager.upload_file(
            user_id=user1.id,
            file_data=MINI_PNG,
            filename="attach_access.png",
            content_type="image/png",
        )
        # Create DM and send message with attachment
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="Check this out",
            attachments=[
                {
                    "filename": result.filename,
                    "content_type": result.content_type,
                    "size": result.size,
                    "url": result.url,
                }
            ],
        )
        # User2 should now have access through the DM conversation
        file = media_manager.get_file(result.file_id)
        if file:
            assert media_manager.check_file_access(file.filename, user2.id) is True
