"""Targeted media access-control regression tests."""

import pytest


@pytest.mark.media
class TestMediaAccessControl:
    """Regression tests for media attachment access matching."""

    def test_check_file_access_ignores_superstring_metadata_ids(
        self, media_module, modules, user_pool, sample_image_bytes
    ):
        """Attachment metadata should not grant access to a different numeric file ID."""
        uploader = user_pool.get_user()
        sender = user_pool.get_user()
        recipient = user_pool.get_user()

        dm = modules.messaging.create_dm(sender.id, recipient.id)
        message = modules.messaging.send_message(sender.id, dm.id, "hello")
        uploaded = media_module.upload_file(
            user_id=uploader.id,
            file_data=sample_image_bytes,
            filename="secret.jpg",
            content_type="image/jpeg",
        )

        modules.messaging.add_attachment(
            user_id=sender.id,
            message_id=message.id,
            filename="different.jpg",
            content_type="image/jpeg",
            size=len(sample_image_bytes),
            url="https://cdn.example.com/different.jpg",
            metadata={"media_file_id": f"{uploaded.file_id}99"},
        )

        assert media_module.check_file_access(uploaded.filename, recipient.id) is False