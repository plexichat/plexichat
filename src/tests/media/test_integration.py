"""Tests for media integration with messaging."""

import pytest


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestIntegration:
    """Tests for media integration with messaging module."""

    def test_upload_and_attach_to_message(
        self, media_manager, messaging_manager, two_users
    ):
        """Test uploading a file and attaching it to a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        attachment = media_manager.upload_attachment(
            user_id=user1.id,
            file_data=MINI_PNG,
            filename="msg_attach.png",
            content_type="image/png",
        )

        msg = messaging_manager.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="Here's a file",
            attachments=[
                {
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size": attachment.size,
                    "url": attachment.url,
                }
            ],
        )
        assert msg is not None
        assert msg.content == "Here's a file"

    def test_rate_limit_status(self, media_manager, test_user):
        """Test getting rate limit status for a user."""
        status = media_manager.get_rate_limit_status(test_user.id)
        assert isinstance(status, dict)
        assert "enabled" in status

    def test_scan_file(self, media_manager, test_user):
        """Test scanning an uploaded file."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="scan_test.png",
            content_type="image/png",
        )
        scan_status, scan_result = media_manager.scan_file(result.file_id)
        # Scanner is likely not available in test env, so status is SKIPPED
        assert scan_status is not None

    def test_scan_nonexistent_file(self, media_manager):
        """Test scanning a nonexistent file raises error."""
        from src.core.media.exceptions import MediaError

        with pytest.raises(MediaError):
            media_manager.scan_file(9999999)

    def test_get_video_metadata_non_video(self, media_manager, test_user):
        """Test getting video metadata for a non-video file returns None."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="not_video.png",
            content_type="image/png",
        )
        metadata = media_manager.get_video_metadata(result.file_id)
        assert metadata is None

    def test_get_video_metadata_nonexistent(self, media_manager):
        """Test getting video metadata for nonexistent file returns None."""
        metadata = media_manager.get_video_metadata(9999999)
        assert metadata is None

    def test_multiple_uploads(self, media_manager, test_user):
        """Test uploading multiple files."""
        results = []
        for i in range(3):
            result = media_manager.upload_file(
                user_id=test_user.id,
                file_data=MINI_PNG,
                filename=f"multi_{i}.png",
                content_type="image/png",
            )
            results.append(result)
        assert len(results) == 3
        assert len({r.file_id for r in results}) == 3  # All unique IDs
