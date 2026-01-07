"""
Integration tests for media module.
"""

import pytest


@pytest.mark.media
class TestMediaModuleSetup:
    """Tests for media module initialization."""

    def test_module_setup(self, modules, temp_upload_dir):
        """Test that media module can be set up."""
        import utils.config as config

        assert config._config_instance is not None
        config._config_instance.config["media"] = {
            "storage_backend": "local",
            "local_path": temp_upload_dir,
            "local_url": "/media",
            "signing_key": "test-key",
        }

        from src.core import media

        media._manager = None
        media._setup_complete = False

        media.setup(modules._db)

        assert media._setup_complete is True
        assert media._manager is not None

    def test_module_raises_without_setup(self):
        """Test that module raises error if not set up."""
        from src.core import media

        media._manager = None
        media._setup_complete = False

        with pytest.raises(RuntimeError):
            media.get_file(1)


@pytest.mark.media
class TestFullUploadWorkflow:
    """Tests for complete upload workflows."""

    def test_upload_retrieve_delete_workflow(
        self, media_module, user_pool, sample_image_bytes
    ):
        """Test complete upload, retrieve, delete workflow."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="workflow_test.jpg",
        )

        assert result.file_id is not None

        file = media_module.get_file(result.file_id)
        assert file is not None
        assert file.original_filename == "workflow_test.jpg"

        data, content_type = media_module.get_file_data(result.file_id)
        assert data == sample_image_bytes
        assert content_type == "image/jpeg"

        deleted = media_module.delete_file(user.id, result.file_id)
        assert deleted is True

        file = media_module.get_file(result.file_id)
        assert file is None

    def test_upload_with_thumbnails_workflow(
        self, media_module, user_pool, sample_image_bytes
    ):
        """Test upload with thumbnail generation workflow."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="thumb_workflow.jpg",
        )

        thumbnails = media_module.get_thumbnails(result.file_id)

        try:
            from PIL import Image  # noqa: F401

            assert len(thumbnails) > 0

            custom_url = media_module.create_thumbnail(result.file_id, size=100)
            assert custom_url is not None
        except ImportError:
            pass

    def test_upload_and_sign_workflow(
        self, media_module, user_pool, sample_image_bytes
    ):
        """Test upload and URL signing workflow."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="sign_workflow.jpg",
        )

        signed = media_module.sign_url(result.file_id, expires_in=3600)

        assert signed.url is not None
        assert signed.file_id == result.file_id

        is_valid, file_id = media_module.verify_signed_url(signed.url)
        assert is_valid is True
        assert file_id == result.file_id


@pytest.mark.media
class TestMessagingIntegration:
    """Tests for integration with messaging module."""

    def test_upload_attachment_format(
        self, media_module, user_pool, sample_image_bytes
    ):
        """Test that upload_attachment returns correct format."""
        user = user_pool.get_user()

        attachment = media_module.upload_attachment(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="msg_attachment.jpg",
        )

        assert hasattr(attachment, "filename")
        assert hasattr(attachment, "content_type")
        assert hasattr(attachment, "size")
        assert hasattr(attachment, "url")
        assert hasattr(attachment, "metadata")

        assert attachment.filename == "msg_attachment.jpg"
        assert attachment.content_type == "image/jpeg"
        assert attachment.size == len(sample_image_bytes)
        assert attachment.url is not None

    def test_attachment_can_be_used_with_messaging(
        self, media_module, modules, user_pool, sample_image_bytes
    ):
        """Test that attachment data can be used with messaging module."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        attachment = media_module.upload_attachment(
            user_id=user1.id,
            file_data=sample_image_bytes,
            filename="chat_image.jpg",
        )

        dm = modules.messaging.create_dm(user1.id, user2.id)

        msg = modules.messaging.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="Check out this image!",
            attachments=[
                {
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size": attachment.size,
                    "url": attachment.url,
                    "metadata": attachment.metadata,
                }
            ],
        )

        assert msg is not None

        attachments = modules.messaging.get_attachments(user1.id, msg.id)
        assert len(attachments) == 1
        assert attachments[0].filename == "chat_image.jpg"


@pytest.mark.media
class TestMultipleFileTypes:
    """Tests for handling multiple file types."""

    def test_upload_different_image_formats(
        self,
        media_module,
        user_pool,
        sample_image_bytes,
        sample_png_bytes,
        sample_gif_bytes,
    ):
        """Test uploading different image formats."""
        user = user_pool.get_user()

        jpeg_result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="test.jpg",
            content_type="image/jpeg",
        )
        assert jpeg_result.content_type == "image/jpeg"

        png_result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_png_bytes,
            filename="test.png",
            content_type="image/png",
        )
        assert png_result.content_type == "image/png"

        gif_result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_gif_bytes,
            filename="test.gif",
            content_type="image/gif",
        )
        assert gif_result.content_type == "image/gif"

    def test_upload_document_types(
        self, media_module, user_pool, sample_text_bytes, sample_pdf_bytes
    ):
        """Test uploading document types."""
        user = user_pool.get_user()

        text_result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_text_bytes,
            filename="readme.txt",
            content_type="text/plain",
        )
        assert text_result.content_type == "text/plain"

        pdf_result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_pdf_bytes,
            filename="document.pdf",
            content_type="application/pdf",
        )
        assert pdf_result.content_type == "application/pdf"


@pytest.mark.media
class TestConcurrentUploads:
    """Tests for concurrent upload handling."""

    def test_multiple_users_upload(self, media_module, user_pool, sample_image_bytes):
        """Test multiple users uploading simultaneously."""
        users = [user_pool.get_user() for _ in range(5)]
        results = []

        for i, user in enumerate(users):
            result = media_module.upload_file(
                user_id=user.id,
                file_data=sample_image_bytes,
                filename=f"user_{i}_file.jpg",
            )
            results.append(result)

        file_ids = [r.file_id for r in results]
        assert len(set(file_ids)) == 5

    def test_same_user_multiple_uploads(
        self, media_module, user_pool, sample_image_bytes
    ):
        """Test same user uploading multiple files."""
        user = user_pool.get_user()
        results = []

        for i in range(5):
            result = media_module.upload_file(
                user_id=user.id,
                file_data=sample_image_bytes,
                filename=f"file_{i}.jpg",
            )
            results.append(result)

        file_ids = [r.file_id for r in results]
        assert len(set(file_ids)) == 5

        for result in results:
            file = media_module.get_file(result.file_id)
            assert file is not None
            assert file.uploaded_by == user.id
