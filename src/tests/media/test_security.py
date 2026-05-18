"""Tests for media security validation (blocked extensions, MIME types, magic bytes)."""

import pytest

from src.core.media.models import MediaType
from src.core.media.exceptions import FileTypeError, FileUploadError


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestSecurity:
    """Tests for media security validation and enforcement."""

    def test_blocked_extension_exe(self, media_manager, test_user):
        """Test that .exe files are blocked."""
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=b"MZ\x90\x00" + b"\x00" * 60,
                filename="malware.exe",
                content_type="application/octet-stream",
            )

    def test_blocked_extension_bat(self, media_manager, test_user):
        """Test that .bat files are blocked."""
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=b"@echo off",
                filename="script.bat",
                content_type="application/octet-stream",
            )

    def test_blocked_extension_sh(self, media_manager, test_user):
        """Test that .sh files are blocked."""
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=b"#!/bin/bash",
                filename="script.sh",
                content_type="application/x-sh",
            )

    def test_mime_spoofing_detected(self, media_manager, test_user):
        """Test that MIME type spoofing is detected via magic bytes."""
        # Try to upload a PNG but claim it's a PDF
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=MINI_PNG,
                filename="fake.pdf",
                content_type="application/pdf",
            )

    def test_validate_magic_bytes_valid_png(self, media_manager):
        """Test magic byte validation for valid PNG."""
        result = media_manager._validate_magic_bytes(MINI_PNG, "image/png")
        assert result is True

    def test_validate_magic_bytes_invalid_png(self, media_manager):
        """Test magic byte validation for invalid PNG."""
        result = media_manager._validate_magic_bytes(b"not a png", "image/png")
        assert result is False

    def test_validate_magic_bytes_text_allowed(self, media_manager):
        """Test that text types have no magic byte enforcement."""
        result = media_manager._validate_magic_bytes(b"hello world", "text/plain")
        assert result is True

    def test_validate_content_type_allowed(self, media_manager):
        """Test that allowed content types pass validation."""
        # Should not raise
        media_manager._validate_content_type("image/png", MediaType.IMAGE)

    def test_validate_content_type_disallowed(self, media_manager):
        """Test that disallowed content types are rejected."""
        with pytest.raises(FileTypeError):
            media_manager._validate_content_type("image/tiff", MediaType.IMAGE)

    def test_validate_file_size_within_limit(self, media_manager):
        """Test that files within size limit pass validation."""
        # Should not raise
        media_manager._validate_file_size(1024, MediaType.IMAGE)

    def test_validate_file_size_exceeds_limit(self, media_manager):
        """Test that files exceeding size limit are rejected."""
        with pytest.raises(FileSizeError):
            media_manager._validate_file_size(100 * 1024 * 1024, MediaType.IMAGE)

    def test_detect_content_type_from_bytes(self, media_manager):
        """Test content type detection from magic bytes."""
        detected = media_manager._detect_content_type(
            MINI_PNG, "application/octet-stream"
        )
        assert detected == "image/png"

    def test_detect_content_type_pdf(self, media_manager):
        """Test PDF content type detection."""
        detected = media_manager._detect_content_type(
            b"%PDF-1.4", "application/octet-stream"
        )
        assert detected == "application/pdf"

    def test_check_file_access_as_uploader(self, media_manager, test_user):
        """Test that the uploader can access their own file."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="access_test.png",
            content_type="image/png",
        )
        file = media_manager.get_file(result.file_id)
        assert file is not None
        assert media_manager.check_file_access(file.filename, test_user.id) is True

    def test_check_file_access_as_other_user(self, media_manager, two_users):
        """Test that non-uploader without conversation access is denied."""
        owner, other = two_users
        result = media_manager.upload_file(
            user_id=owner.id,
            file_data=MINI_PNG,
            filename="other_test.png",
            content_type="image/png",
        )
        file = media_manager.get_file(result.file_id)
        assert file is not None
        # Other user should not have access (no conversation attachment)
        assert media_manager.check_file_access(file.filename, other.id) is False
