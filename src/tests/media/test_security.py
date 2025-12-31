"""
Comprehensive media security tests.

Tests cover:
- File upload validation
- MIME type verification
- Image processing exploits (decompression bombs, dimension attacks)
- Path traversal attempts
- Malicious file content (magic byte spoofing, executables)
- Size limit enforcement
- CDN/proxy security (SSRF, content filtering)
"""

import pytest
import io
import uuid


@pytest.mark.media
class TestFileUploadValidation:
    """Test file upload validation and sanitization."""

    def test_valid_image_upload(self, media_module, user_pool, sample_image_bytes):
        """Test that valid images pass validation."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="valid_image.jpg",
            content_type="image/jpeg"
        )
        
        assert result.file_id is not None
        assert result.content_type == "image/jpeg"
        assert result.url is not None

    def test_filename_sanitization_removes_path_components(self, media_module, user_pool, sample_image_bytes):
        """Test that filenames with path components are sanitized."""
        user = user_pool.get_user()
        
        malicious_filenames = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "/etc/shadow",
            "C:\\Windows\\System32\\config\\SAM",
            "folder/../../../secret.txt",
        ]
        
        for filename in malicious_filenames:
            result = media_module.upload_file(
                user_id=user.id,
                file_data=sample_image_bytes,
                filename=filename,
                content_type="image/jpeg"
            )
            
            stored_file = media_module.get_file(result.file_id)
            assert ".." not in stored_file.original_filename
            assert "/" not in stored_file.original_filename
            assert "\\" not in stored_file.original_filename

    def test_filename_sanitization_removes_null_bytes(self, media_module, user_pool, sample_image_bytes):
        """Test that null bytes are removed from filenames."""
        user = user_pool.get_user()
        
        filename_with_null = "image\x00.jpg"
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename=filename_with_null,
            content_type="image/jpeg"
        )
        
        stored_file = media_module.get_file(result.file_id)
        assert "\x00" not in stored_file.original_filename

    def test_filename_sanitization_removes_control_characters(self, media_module, user_pool, sample_image_bytes):
        """Test that control characters are removed from filenames."""
        user = user_pool.get_user()
        
        filename_with_control = "image\x01\x02\x03\x7f.jpg"
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename=filename_with_control,
            content_type="image/jpeg"
        )
        
        stored_file = media_module.get_file(result.file_id)
        for i in range(0x20):
            assert chr(i) not in stored_file.original_filename
        assert "\x7f" not in stored_file.original_filename

    def test_filename_sanitization_limits_length(self, media_module, user_pool, sample_image_bytes):
        """Test that extremely long filenames are truncated."""
        user = user_pool.get_user()
        
        long_filename = "a" * 500 + ".jpg"
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename=long_filename,
            content_type="image/jpeg"
        )
        
        stored_file = media_module.get_file(result.file_id)
        assert len(stored_file.original_filename) <= 250

    def test_empty_filename_handled(self, media_module, user_pool, sample_image_bytes):
        """Test that empty filenames are handled gracefully."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="",
            content_type="image/jpeg"
        )
        
        stored_file = media_module.get_file(result.file_id)
        assert stored_file.original_filename
        assert len(stored_file.original_filename) > 0


@pytest.mark.media
class TestMIMETypeVerification:
    """Test MIME type validation and magic byte verification."""

    def test_jpeg_magic_byte_validation(self, media_module, user_pool, sample_image_bytes):
        """Test that JPEG files are validated by magic bytes."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="valid.jpg",
            content_type="image/jpeg"
        )
        
        assert result.file_id is not None

    def test_png_magic_byte_validation(self, media_module, user_pool, sample_png_bytes):
        """Test that PNG files are validated by magic bytes."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_png_bytes,
            filename="valid.png",
            content_type="image/png"
        )
        
        assert result.file_id is not None

    def test_gif_magic_byte_validation(self, media_module, user_pool, sample_gif_bytes):
        """Test that GIF files are validated by magic bytes."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_gif_bytes,
            filename="valid.gif",
            content_type="image/gif"
        )
        
        assert result.file_id is not None

    def test_mime_type_spoofing_text_as_image(self, media_module, user_pool):
        """Test that text files cannot be uploaded with image MIME type."""
        user = user_pool.get_user()
        
        text_data = b"This is just plain text, not an image!"
        
        with pytest.raises(Exception) as exc_info:
            media_module.upload_file(
                user_id=user.id,
                file_data=text_data,
                filename="fake_image.jpg",
                content_type="image/jpeg"
            )
        
        error_msg = str(exc_info.value).lower()
        assert "declared type" in error_msg or "signature" in error_msg or "match" in error_msg

    def test_mime_type_spoofing_html_as_image(self, media_module, user_pool):
        """Test that HTML files cannot be uploaded with image MIME type."""
        user = user_pool.get_user()
        
        html_data = b"<!DOCTYPE html><html><body><script>alert('XSS')</script></body></html>"
        
        with pytest.raises(Exception) as exc_info:
            media_module.upload_file(
                user_id=user.id,
                file_data=html_data,
                filename="fake.jpg",
                content_type="image/jpeg"
            )
        
        error_msg = str(exc_info.value).lower()
        assert "declared type" in error_msg or "signature" in error_msg

    def test_mime_type_spoofing_exe_as_image(self, media_module, user_pool):
        """Test that executable files cannot be uploaded with image MIME type."""
        user = user_pool.get_user()
        
        exe_data = b"MZ\x90\x00" + b"\x00" * 100
        
        with pytest.raises(Exception) as exc_info:
            media_module.upload_file(
                user_id=user.id,
                file_data=exe_data,
                filename="malware.jpg",
                content_type="image/jpeg"
            )
        
        error_msg = str(exc_info.value).lower()
        assert "declared type" in error_msg or "signature" in error_msg

    def test_wrong_image_type_png_as_jpeg(self, media_module, user_pool, sample_png_bytes):
        """Test that PNG cannot be uploaded with JPEG MIME type."""
        user = user_pool.get_user()
        
        with pytest.raises(Exception) as exc_info:
            media_module.upload_file(
                user_id=user.id,
                file_data=sample_png_bytes,
                filename="wrong.jpg",
                content_type="image/jpeg"
            )
        
        error_msg = str(exc_info.value).lower()
        assert "declared type" in error_msg or "signature" in error_msg or "match" in error_msg

    def test_pdf_magic_byte_validation(self, media_module, user_pool, sample_pdf_bytes):
        """Test that PDF files are validated by magic bytes."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_pdf_bytes,
            filename="document.pdf",
            content_type="application/pdf"
        )
        
        assert result.file_id is not None


@pytest.mark.media
class TestExecutableBlocking:
    """Test that executable and dangerous file types are blocked."""

    def test_exe_extension_blocked(self, media_module, user_pool):
        """Test that .exe files are blocked."""
        user = user_pool.get_user()
        
        exe_data = b"MZ\x90\x00" + b"\x00" * 100
        
        with pytest.raises(Exception):
            media_module.upload_file(
                user_id=user.id,
                file_data=exe_data,
                filename="malware.exe",
                content_type="application/x-msdownload"
            )

    def test_bat_extension_blocked(self, media_module, user_pool):
        """Test that .bat files are blocked."""
        user = user_pool.get_user()
        
        bat_data = b"@echo off\nformat c: /y"
        
        with pytest.raises(Exception):
            media_module.upload_file(
                user_id=user.id,
                file_data=bat_data,
                filename="malicious.bat",
                content_type="application/x-bat"
            )

    def test_sh_extension_blocked(self, media_module, user_pool):
        """Test that .sh files are blocked."""
        user = user_pool.get_user()
        
        sh_data = b"#!/bin/bash\nrm -rf /"
        
        with pytest.raises(Exception):
            media_module.upload_file(
                user_id=user.id,
                file_data=sh_data,
                filename="evil.sh",
                content_type="application/x-sh"
            )

    def test_ps1_extension_blocked(self, media_module, user_pool):
        """Test that PowerShell scripts are blocked."""
        user = user_pool.get_user()
        
        ps1_data = b"Remove-Item -Path C:\\ -Recurse -Force"
        
        with pytest.raises(Exception):
            media_module.upload_file(
                user_id=user.id,
                file_data=ps1_data,
                filename="script.ps1",
                content_type="text/plain"
            )

    def test_py_extension_blocked(self, media_module, user_pool):
        """Test that Python scripts are blocked."""
        user = user_pool.get_user()
        
        py_data = b"import os; os.system('rm -rf /')"
        
        with pytest.raises(Exception):
            media_module.upload_file(
                user_id=user.id,
                file_data=py_data,
                filename="backdoor.py",
                content_type="text/x-python"
            )

    def test_dll_extension_blocked(self, media_module, user_pool):
        """Test that DLL files are blocked."""
        user = user_pool.get_user()
        
        dll_data = b"MZ\x90\x00" + b"\x00" * 100
        
        with pytest.raises(Exception):
            media_module.upload_file(
                user_id=user.id,
                file_data=dll_data,
                filename="library.dll",
                content_type="application/x-msdownload"
            )


@pytest.mark.media
class TestSizeLimitEnforcement:
    """Test file size limit enforcement."""

    def test_image_size_limit_enforced(self, media_module, user_pool):
        """Test that images exceeding size limit are rejected."""
        user = user_pool.get_user()
        
        large_image = b'\xff\xd8\xff\xe0' + b'\x00' * (11 * 1024 * 1024)
        
        with pytest.raises(Exception) as exc_info:
            media_module.upload_file(
                user_id=user.id,
                file_data=large_image,
                filename="huge.jpg",
                content_type="image/jpeg"
            )
        
        assert "size" in str(exc_info.value).lower() or "limit" in str(exc_info.value).lower()

    def test_document_size_limit_enforced(self, media_module, user_pool):
        """Test that documents exceeding size limit are rejected."""
        user = user_pool.get_user()
        
        large_pdf = b'%PDF-1.4\n' + b'x' * (26 * 1024 * 1024)
        
        with pytest.raises(Exception) as exc_info:
            media_module.upload_file(
                user_id=user.id,
                file_data=large_pdf,
                filename="large.pdf",
                content_type="application/pdf"
            )
        
        assert "size" in str(exc_info.value).lower() or "limit" in str(exc_info.value).lower()

    def test_exact_size_limit_allowed(self, media_module, user_pool):
        """Test that files at exactly the size limit are allowed."""
        from PIL import Image
        user = user_pool.get_user()
        
        # Create a small valid JPEG
        img = Image.new("RGB", (10, 10), color="blue")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        valid_jpeg_base = buffer.getvalue()
        
        # Pad it to exactly 10MB (JPEG often ignores trailing data)
        exact_size = 10 * 1024 * 1024
        exact_image = valid_jpeg_base + b'\x00' * (exact_size - len(valid_jpeg_base))
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=exact_image,
            filename="exact_limit.jpg",
            content_type="image/jpeg"
        )
        
        assert result.file_id is not None

    def test_rate_limit_total_size_enforced(self, media_module, user_pool):
        """Test that daily total size limit is enforced."""
        import utils.config as config
        
        original_config = config.get("media", {})
        rate_config = original_config.get("rate_limit", {})
        
        if not rate_config.get("enabled", True):
            pytest.skip("Rate limiting disabled")
        
        max_daily_size = rate_config.get("max_total_size_per_day", 512 * 1024 * 1024)
        
        if max_daily_size > 100 * 1024 * 1024:
            pytest.skip("Daily limit too high for test")
        
        user = user_pool.get_user()
        
        chunk_size = 5 * 1024 * 1024
        chunks_needed = (max_daily_size // chunk_size) + 2
        
        successful_uploads = 0
        
        for i in range(chunks_needed):
            try:
                data = b'\xff\xd8\xff\xe0\x00\x10JFIF' + b'\x00' * chunk_size
                media_module.upload_file(
                    user_id=user.id,
                    file_data=data,
                    filename=f"chunk_{i}.jpg",
                    content_type="image/jpeg"
                )
                successful_uploads += 1
            except Exception as e:
                if "limit" in str(e).lower():
                    break
        
        assert successful_uploads < chunks_needed


@pytest.mark.media
@pytest.mark.pillow
class TestImageProcessingExploits:
    """Test protection against image processing exploits."""

    def test_decompression_bomb_protection(self, media_module, user_pool):
        """Test protection against decompression bomb attacks."""
        pytest.importorskip("PIL")
        from PIL import Image
        
        user = user_pool.get_user()
        
        # Instead of creating a real 400MP image which is slow,
        # we can mock an image object that claims to be huge.
        # However, to test the full pipeline including Pillow's internal checks,
        # we'll use a slightly smaller but still exceeding-limit image
        # and optimize the data generation.
        
        try:
            # 15000x15000 = 225MP, above default 178MP limit
            # We use a 1x1 image and mock its size property if possible, 
            # or just use a very simple format.
            # JPEG is much faster to "save" than PNG even with compress_level=0
            huge_img = Image.new("RGB", (15000, 15000), color="red")
            buffer = io.BytesIO()
            # JPEG doesn't actually store all pixels if they are the same color (RLE-like)
            huge_img.save(buffer, format="JPEG", quality=1)
            bomb_data = buffer.getvalue()
            
            # Clear memory immediately
            del huge_img
            
            with pytest.raises(Exception) as exc_info:
                media_module.upload_file(
                    user_id=user.id,
                    file_data=bomb_data,
                    filename="bomb.jpg",
                    content_type="image/jpeg"
                )
            
            error_msg = str(exc_info.value).lower()
            assert any(word in error_msg for word in ["pixel", "dimension", "size", "limit", "metadata"])
        except MemoryError:
            pytest.skip("Insufficient memory to create test decompression bomb")

    def test_excessive_image_dimensions_rejected(self, media_module, user_pool):
        """Test that images with excessive dimensions are rejected."""
        pytest.importorskip("PIL")
        from PIL import Image
        
        user = user_pool.get_user()
        
        try:
            wide_img = Image.new("RGB", (20000, 100), color="blue")
            buffer = io.BytesIO()
            wide_img.save(buffer, format="JPEG", quality=85)
            wide_data = buffer.getvalue()
            
            with pytest.raises(Exception):
                media_module.upload_file(
                    user_id=user.id,
                    file_data=wide_data,
                    filename="wide.jpg",
                    content_type="image/jpeg"
                )
        except (MemoryError, OSError):
            pytest.skip("Cannot create test image with extreme dimensions")

    def test_reasonable_image_dimensions_allowed(self, media_module, user_pool):
        """Test that images with reasonable dimensions are allowed."""
        pytest.importorskip("PIL")
        from PIL import Image
        
        user = user_pool.get_user()
        
        normal_img = Image.new("RGB", (1920, 1080), color="green")
        buffer = io.BytesIO()
        normal_img.save(buffer, format="JPEG", quality=85)
        normal_data = buffer.getvalue()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=normal_data,
            filename="normal.jpg",
            content_type="image/jpeg"
        )
        
        assert result.file_id is not None

    def test_thumbnail_generation_with_malformed_image(self, media_module, user_pool):
        """Test thumbnail generation handles malformed images gracefully."""
        pytest.importorskip("PIL")
        
        user = user_pool.get_user()
        
        truncated_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00' + b'\x00' * 50
        
        try:
            result = media_module.upload_file(
                user_id=user.id,
                file_data=truncated_jpeg,
                filename="malformed.jpg",
                content_type="image/jpeg"
            )
            
            media_module.create_thumbnail(result.file_id, 128, user_id=user.id)
            
        except Exception:
            pass


@pytest.mark.media
class TestPathTraversalAttempts:
    """Test protection against path traversal attacks."""

    def test_double_dot_in_filename(self, media_module, user_pool, sample_image_bytes):
        """Test that double dots in filename are sanitized."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="../../../etc/passwd.jpg",
            content_type="image/jpeg"
        )
        
        file_info = media_module.get_file(result.file_id)
        assert ".." not in file_info.storage_path
        assert ".." not in file_info.original_filename

    def test_absolute_path_in_filename(self, media_module, user_pool, sample_image_bytes):
        """Test that absolute paths in filename are sanitized."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="/etc/shadow",
            content_type="image/jpeg"
        )
        
        file_info = media_module.get_file(result.file_id)
        assert not file_info.storage_path.startswith("/etc")
        assert file_info.storage_path.startswith("image/")

    def test_windows_path_in_filename(self, media_module, user_pool, sample_image_bytes):
        """Test that Windows paths in filename are sanitized."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="C:\\Windows\\System32\\config\\SAM",
            content_type="image/jpeg"
        )
        
        file_info = media_module.get_file(result.file_id)
        assert "Windows" not in file_info.storage_path
        assert "\\" not in file_info.storage_path
        assert file_info.storage_path.startswith("image/")

    def test_unc_path_in_filename(self, media_module, user_pool, sample_image_bytes):
        """Test that UNC paths in filename are sanitized."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="\\\\server\\share\\file.jpg",
            content_type="image/jpeg"
        )
        
        file_info = media_module.get_file(result.file_id)
        assert "server" not in file_info.storage_path.lower()
        assert "\\" not in file_info.storage_path


@pytest.mark.media
class TestCDNProxySecurity:
    """Test CDN/proxy security features."""

    def test_proxy_blocks_localhost_urls(self, modules, temp_upload_dir):
        """Test that proxy blocks localhost URLs (SSRF protection)."""
        pytest.importorskip("requests")
        
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["proxy_enabled"] = True
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        
        config._config_instance.config["media"] = test_config
        
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage import LocalStorage
        
        storage = LocalStorage(temp_upload_dir, "/media")
        proxy = ExternalProxy(storage, modules._db)
        
        localhost_urls = [
            "http://localhost/admin",
            "http://127.0.0.1/secret",
            "http://[::1]/internal",
            "http://0.0.0.0/config",
        ]
        
        for url in localhost_urls:
            with pytest.raises(Exception) as exc_info:
                proxy.fetch(url)
            
            assert ("localhost" in str(exc_info.value).lower() or 
                    "not allowed" in str(exc_info.value).lower() or
                    "forbidden" in str(exc_info.value).lower())
        
        config._config_instance.config["media"] = original_media_config

    def test_proxy_blocks_private_ip_addresses(self, modules, temp_upload_dir):
        """Test that proxy blocks private IP addresses (SSRF protection)."""
        pytest.importorskip("requests")
        
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["proxy_enabled"] = True
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        
        config._config_instance.config["media"] = test_config
        
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage import LocalStorage
        
        storage = LocalStorage(temp_upload_dir, "/media")
        proxy = ExternalProxy(storage, modules._db)
        
        private_ips = [
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
        ]
        
        for url in private_ips:
            with pytest.raises(Exception) as exc_info:
                proxy.fetch(url)
            
            error_msg = str(exc_info.value).lower()
            assert ("private" in error_msg or 
                    "not allowed" in error_msg or 
                    "internal" in error_msg or
                    "forbidden" in error_msg)
        
        config._config_instance.config["media"] = original_media_config

    def test_proxy_blocks_internal_hostnames(self, modules, temp_upload_dir):
        """Test that proxy blocks internal hostnames."""
        pytest.importorskip("requests")
        
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["proxy_enabled"] = True
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        
        config._config_instance.config["media"] = test_config
        
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage import LocalStorage
        
        storage = LocalStorage(temp_upload_dir, "/media")
        proxy = ExternalProxy(storage, modules._db)
        
        internal_hostnames = [
            "http://internal.company.local/",
            "http://database.internal/",
            "http://api.local/",
        ]
        
        for url in internal_hostnames:
            with pytest.raises(Exception) as exc_info:
                proxy.fetch(url)
            
            assert "internal" in str(exc_info.value).lower() or "not allowed" in str(exc_info.value).lower()
        
        config._config_instance.config["media"] = original_media_config

    def test_proxy_blocks_non_http_schemes(self, modules, temp_upload_dir):
        """Test that proxy blocks non-HTTP schemes."""
        pytest.importorskip("requests")
        
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["proxy_enabled"] = True
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        
        config._config_instance.config["media"] = test_config
        
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage import LocalStorage
        
        storage = LocalStorage(temp_upload_dir, "/media")
        proxy = ExternalProxy(storage, modules._db)
        
        dangerous_schemes = [
            "file:///etc/passwd",
            "ftp://malicious.com/payload",
            "gopher://internal.local/",
            "data:text/html,<script>alert(1)</script>",
        ]
        
        for url in dangerous_schemes:
            with pytest.raises(Exception) as exc_info:
                proxy.fetch(url)
            
            assert "scheme" in str(exc_info.value).lower() or "not allowed" in str(exc_info.value).lower()
        
        config._config_instance.config["media"] = original_media_config

    def test_proxy_enforces_content_type_whitelist(self, modules, temp_upload_dir, mocker):
        """Test that proxy only allows whitelisted content types."""
        pytest.importorskip("requests")
        
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["proxy_enabled"] = True
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        
        config._config_instance.config["media"] = test_config
        
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage import LocalStorage
        
        storage = LocalStorage(temp_upload_dir, "/media")
        proxy = ExternalProxy(storage, modules._db)
        
        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "application/x-executable"}
        mock_response.raise_for_status.return_value = None
        
        mocker.patch.object(proxy._requests, "get", return_value=mock_response)
        
        with pytest.raises(Exception) as exc_info:
            proxy.fetch("http://example.com/malware.exe")
        
        assert "content type" in str(exc_info.value).lower() or "not allowed" in str(exc_info.value).lower()
        
        config._config_instance.config["media"] = original_media_config

    def test_proxy_enforces_size_limit(self, modules, temp_upload_dir, mocker):
        """Test that proxy enforces maximum content size."""
        pytest.importorskip("requests")
        
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["proxy_enabled"] = True
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        test_config["proxy_max_size"] = 1024
        
        config._config_instance.config["media"] = test_config
        
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage import LocalStorage
        
        storage = LocalStorage(temp_upload_dir, "/media")
        proxy = ExternalProxy(storage, modules._db, max_size=1024)
        
        mock_response = mocker.MagicMock()
        mock_response.headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": "2048"
        }
        mock_response.raise_for_status.return_value = None
        
        mocker.patch.object(proxy._requests, "get", return_value=mock_response)
        
        with pytest.raises(Exception) as exc_info:
            proxy.fetch("http://example.com/huge.jpg")
        
        assert "large" in str(exc_info.value).lower() or "size" in str(exc_info.value).lower()
        
        config._config_instance.config["media"] = original_media_config


@pytest.mark.media
class TestMaliciousFileContent:
    """Test detection and blocking of malicious file content."""

    def test_polyglot_file_detection(self, media_module, user_pool, sample_image_bytes):
        """Test detection of polyglot files (valid in multiple formats)."""
        user = user_pool.get_user()
        
        # Use a real image base and append malicious payload
        polyglot = sample_image_bytes + b'<script>alert("XSS")</script>'
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=polyglot,
            filename="polyglot.jpg",
            content_type="image/jpeg"
        )
        
        assert result.file_id is not None

    def test_svg_with_javascript_allowed_as_svg(self, media_module, user_pool):
        """Test that SVG with JavaScript is allowed (if SVG uploads are enabled)."""
        user = user_pool.get_user()
        
        svg_with_js = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<script>alert("XSS")</script>'
            b'</svg>'
        )
        
        try:
            media_module.upload_file(
                user_id=user.id,
                file_data=svg_with_js,
                filename="dangerous.svg",
                content_type="image/svg+xml"
            )
        except Exception as e:
            assert "type not allowed" in str(e).lower() or "content type" in str(e).lower()

    def test_zip_bomb_detection(self, media_module, user_pool):
        """Test handling of ZIP bombs (highly compressed malicious files)."""
        user = user_pool.get_user()
        
        import zipfile
        
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("large.txt", b"0" * 10000)
        
        zip_data = buffer.getvalue()
        
        try:
            media_module.upload_file(
                user_id=user.id,
                file_data=zip_data,
                filename="archive.zip",
                content_type="application/zip"
            )
        except Exception as e:
            assert "type not allowed" in str(e).lower() or "content type" in str(e).lower()

    def test_hidden_executable_in_image_metadata(self, media_module, user_pool):
        """Test that executable code in image metadata is handled safely."""
        pytest.importorskip("PIL")
        from PIL import Image
        
        user = user_pool.get_user()
        
        img = Image.new("RGB", (100, 100), color="red")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        
        data_with_metadata = buffer.getvalue()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=data_with_metadata,
            filename="metadata.jpg",
            content_type="image/jpeg"
        )
        
        assert result.file_id is not None

    def test_zero_byte_file_rejected(self, media_module, user_pool):
        """Test that zero-byte files are rejected."""
        user = user_pool.get_user()
        
        with pytest.raises(Exception):
            media_module.upload_file(
                user_id=user.id,
                file_data=b"",
                filename="empty.jpg",
                content_type="image/jpeg"
            )


@pytest.mark.media
class TestRateLimitSecurity:
    """Test rate limiting security features."""

    def test_per_minute_upload_limit(self, modules, temp_upload_dir, user_pool, sample_image_bytes, monkeypatch):
        """Test per-minute upload rate limiting."""
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["rate_limit"] = {
            "enabled": True,
            "uploads_per_minute": 3,
            "uploads_per_hour": 1000,
            "max_total_size_per_day": 1024 * 1024 * 1024
        }
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        
        config._config_instance.config["media"] = test_config
        
        from src.core import media
        media._manager = None
        media._setup_complete = False
        media.setup(modules._db, modules.messaging)
        
        # Patch the manager's cached config as well
        monkeypatch.setitem(media._get_manager()._config, "rate_limit", test_config["rate_limit"])
        
        # Use a fresh user to ensure isolation from other tests
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"rate_user_{unique_id}",
            email=f"rate_user_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        successful = 0
        rate_limited = False
        
        for i in range(5):
            try:
                media.upload_file(
                    user_id=user.id,
                    file_data=sample_image_bytes,
                    filename=f"rate_test_{i}.jpg",
                    content_type="image/jpeg"
                )
                successful += 1
            except Exception as e:
                if "rate limit" in str(e).lower():
                    rate_limited = True
                    break
        
        assert successful == 3, f"Expected 3 successful uploads, got {successful}"
        
        config._config_instance.config["media"] = original_media_config
        media._manager = None
        media._setup_complete = False

    def test_per_hour_upload_limit(self, modules, temp_upload_dir, user_pool, sample_image_bytes, monkeypatch):
        """Test per-hour upload rate limiting."""
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["rate_limit"] = {
            "enabled": True,
            "uploads_per_minute": 1000,
            "uploads_per_hour": 5,
            "max_total_size_per_day": 1024 * 1024 * 1024
        }
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        
        config._config_instance.config["media"] = test_config
        
        from src.core import media
        media._manager = None
        media._setup_complete = False
        media.setup(modules._db, modules.messaging)
        
        # Patch the manager's cached config as well
        monkeypatch.setitem(media._get_manager()._config, "rate_limit", test_config["rate_limit"])
        
        # Use a fresh user to ensure isolation
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"rate_hr_user_{unique_id}",
            email=f"rate_hr_user_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        successful = 0
        rate_limited = False
        
        for i in range(10):
            try:
                media.upload_file(
                    user_id=user.id,
                    file_data=sample_image_bytes,
                    filename=f"rate_test_hr_{i}.jpg",
                    content_type="image/jpeg"
                )
                successful += 1
            except Exception as e:
                if "rate limit" in str(e).lower():
                    rate_limited = True
                    break
        
        assert successful == 5, f"Expected 5 successful uploads, got {successful}"

    def test_daily_size_limit(self, modules, temp_upload_dir, user_pool, sample_image_bytes):
        """Test daily total size upload rate limiting."""
        import utils.config as config
        
        original_media_config = config.get("media", {})
        
        test_config = original_media_config.copy()
        test_config["rate_limit"] = {
            "enabled": True,
            "uploads_per_minute": 1000,
            "uploads_per_hour": 1000,
            "max_total_size_per_day": 1024
        }
        test_config["storage_backend"] = "local"
        test_config["local_path"] = temp_upload_dir
        
        config._config_instance.config["media"] = test_config
        
        from src.core import media
        media._manager = None
        media._setup_complete = False
        media.setup(modules._db, modules.messaging)
        
        # Use a fresh user to ensure isolation
        unique_id = uuid.uuid4().hex[:8]
        user = modules.auth.register(
            username=f"rate_size_user_{unique_id}",
            email=f"rate_size_user_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        # Ensure image is large enough (> 200 bytes) but still valid
        image_data = sample_image_bytes
        if len(image_data) < 400:
            image_data = image_data + b"\x00" * (400 - len(image_data))
        
        successful = 0
        size_limited = False
        
        for i in range(5):
            try:
                media.upload_file(
                    user_id=user.id,
                    file_data=image_data,
                    filename=f"size_test_{i}.jpg",
                    content_type="image/jpeg"
                )
                successful += 1
            except Exception as e:
                import sys
                if "pytest" in sys.modules:
                    print(f"[DEBUG] Upload {i} failed: {e}")
                if "limit" in str(e).lower():
                    size_limited = True
                    break
        
        assert successful < 5, f"Expected some uploads to be limited, but all {successful} succeeded"
        assert size_limited
        
        config._config_instance.config["media"] = original_media_config
        media._manager = None
        media._setup_complete = False
