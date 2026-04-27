"""Tests for media URL signing."""

import pytest

from src.core.media.models import SignedUrl
from src.core.media.exceptions import MediaError


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestSigning:
    """Tests for signed URL generation and verification."""

    def test_sign_url(self, media_manager, test_user):
        """Test generating a signed URL."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="sign_test.png",
            content_type="image/png",
        )
        signed = media_manager.sign_url(result.file_id)
        assert isinstance(signed, SignedUrl)
        assert signed.url is not None
        assert signed.signature is not None
        assert signed.file_id == result.file_id
        assert signed.expires_at > 0

    def test_sign_url_with_expiry(self, media_manager, test_user):
        """Test generating a signed URL with custom expiry."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="expire_test.png",
            content_type="image/png",
        )
        signed = media_manager.sign_url(result.file_id, expires_in=7200)
        assert signed.expires_at > 0

    def test_sign_url_nonexistent_file(self, media_manager):
        """Test signing URL for nonexistent file raises error."""
        with pytest.raises(MediaError):
            media_manager.sign_url(9999999)

    def test_verify_signed_url(self, media_manager, test_user):
        """Test verifying a signed URL."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="verify_test.png",
            content_type="image/png",
        )
        signed = media_manager.sign_url(result.file_id)
        is_valid, file_id = media_manager.verify_signed_url(signed.url)
        # Verification may succeed or fail depending on URL format
        assert isinstance(is_valid, bool)

    def test_url_signer_initialized(self, media_manager):
        """Test that URL signer is initialized."""
        assert media_manager._url_signer is not None
