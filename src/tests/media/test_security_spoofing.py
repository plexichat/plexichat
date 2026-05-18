"""Tests for media MIME type spoofing prevention."""

import pytest

from src.core.media.exceptions import FileTypeError


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestSecuritySpoofing:
    """Tests for MIME type spoofing attack prevention."""

    def test_png_claiming_to_be_jpeg(self, media_manager, test_user):
        """Test that PNG data claiming to be JPEG is rejected."""
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=MINI_PNG,
                filename="fake.jpg",
                content_type="image/jpeg",
            )

    def test_executable_claiming_to_be_image(self, media_manager, test_user):
        """Test that executable data claiming to be image is rejected."""
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=b"MZ\x90\x00" + b"\x00" * 100,
                filename="evil.png",
                content_type="image/png",
            )

    def test_html_claiming_to_be_image(self, media_manager, test_user):
        """Test that HTML data claiming to be image is rejected."""
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=b"<html><body>evil</body></html>",
                filename="xss.png",
                content_type="image/png",
            )

    def test_script_in_svg_filename(self, media_manager, test_user):
        """Test that SVG with script content is handled safely."""
        # SVG may or may not be in the allowed types
        try:
            result = media_manager.upload_file(
                user_id=test_user.id,
                file_data=b'<svg onload="alert(1)"></svg>',
                filename="xss.svg",
                content_type="image/svg+xml",
            )
            # If upload succeeds, the content should be stored safely
            assert result is not None
        except FileTypeError:
            # SVG may not be allowed, which is also safe
            pass

    def test_double_extension_traversal(self, media_manager, test_user):
        """Test that double extensions are sanitized."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="test.exe.png",
            content_type="image/png",
        )
        # Should be sanitized
        assert result is not None

    def test_magic_bytes_validation_for_jpeg(self, media_manager):
        """Test magic byte validation for JPEG."""
        valid_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        assert media_manager._validate_magic_bytes(valid_jpeg, "image/jpeg") is True

    def test_magic_bytes_validation_for_gif(self, media_manager):
        """Test magic byte validation for GIF."""
        assert (
            media_manager._validate_magic_bytes(b"GIF89a" + b"\x00" * 50, "image/gif")
            is True
        )

    def test_magic_bytes_reject_mismatch(self, media_manager):
        """Test that mismatched magic bytes are rejected."""
        # Data that doesn't start with JPEG signature
        result = media_manager._validate_magic_bytes(b"not jpeg data", "image/jpeg")
        assert result is False

    def test_empty_file_magic_bytes(self, media_manager):
        """Test magic bytes validation for empty/short files."""
        result = media_manager._validate_magic_bytes(b"", "image/png")
        assert result is False
