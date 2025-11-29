"""
Tests for URL signing functionality.
"""

import time
import pytest


@pytest.mark.media
class TestUrlSigner:
    """Tests for UrlSigner class."""

    def test_sign_url(self):
        """Test signing a URL."""
        from src.core.media.security.signing import UrlSigner
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=3600)
        
        signed = signer.sign_url(
            url="https://example.com/file.jpg",
            file_id=12345,
        )
        
        assert signed.url is not None
        assert "sig=" in signed.url
        assert "exp=" in signed.url
        assert "fid=" in signed.url
        assert signed.file_id == 12345
        assert signed.signature is not None

    def test_verify_valid_url(self):
        """Test verifying a valid signed URL."""
        from src.core.media.security.signing import UrlSigner
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=3600)
        
        signed = signer.sign_url(
            url="https://example.com/file.jpg",
            file_id=12345,
        )
        
        is_valid, file_id = signer.verify_url(signed.url)
        
        assert is_valid is True
        assert file_id == 12345

    def test_verify_expired_url(self):
        """Test that expired URLs are rejected."""
        from src.core.media.security.signing import UrlSigner
        from src.core.media.exceptions import SignatureExpiredError
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=1)
        
        signed = signer.sign_url(
            url="https://example.com/file.jpg",
            file_id=12345,
            expires_in=-1,
        )
        
        with pytest.raises(SignatureExpiredError):
            signer.verify_url(signed.url)

    def test_verify_tampered_url(self):
        """Test that tampered URLs are rejected."""
        from src.core.media.security.signing import UrlSigner
        from src.core.media.exceptions import SignatureInvalidError
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=3600)
        
        signed = signer.sign_url(
            url="https://example.com/file.jpg",
            file_id=12345,
        )
        
        tampered_url = signed.url.replace("file.jpg", "other.jpg")
        
        with pytest.raises(SignatureInvalidError):
            signer.verify_url(tampered_url)

    def test_verify_wrong_secret(self):
        """Test that URLs signed with different secret are rejected."""
        from src.core.media.security.signing import UrlSigner
        from src.core.media.exceptions import SignatureInvalidError
        
        signer1 = UrlSigner(secret_key="secret-one", default_expiry=3600)
        signer2 = UrlSigner(secret_key="secret-two", default_expiry=3600)
        
        signed = signer1.sign_url(
            url="https://example.com/file.jpg",
            file_id=12345,
        )
        
        with pytest.raises(SignatureInvalidError):
            signer2.verify_url(signed.url)

    def test_custom_expiry(self):
        """Test custom expiration time."""
        from src.core.media.security.signing import UrlSigner
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=3600)
        
        signed = signer.sign_url(
            url="https://example.com/file.jpg",
            file_id=12345,
            expires_in=7200,
        )
        
        expected_expiry = int(time.time()) + 7200
        actual_expiry = signer.get_expiry_time(signed.url)
        
        assert abs(actual_expiry - expected_expiry) < 5

    def test_get_expiry_time(self):
        """Test getting expiry time from signed URL."""
        from src.core.media.security.signing import UrlSigner
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=3600)
        
        signed = signer.sign_url(
            url="https://example.com/file.jpg",
            file_id=12345,
        )
        
        expiry = signer.get_expiry_time(signed.url)
        
        assert expiry is not None
        assert expiry > int(time.time())

    def test_is_expired(self):
        """Test checking if URL is expired."""
        from src.core.media.security.signing import UrlSigner
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=3600)
        
        signed = signer.sign_url(
            url="https://example.com/file.jpg",
            file_id=12345,
        )
        
        assert signer.is_expired(signed.url) is False

    def test_missing_signature_params(self):
        """Test that URLs without signature params are rejected."""
        from src.core.media.security.signing import UrlSigner
        from src.core.media.exceptions import SignatureInvalidError
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=3600)
        
        with pytest.raises(SignatureInvalidError):
            signer.verify_url("https://example.com/file.jpg")

    def test_preserves_existing_query_params(self):
        """Test that existing query params are preserved."""
        from src.core.media.security.signing import UrlSigner
        
        signer = UrlSigner(secret_key="test-secret", default_expiry=3600)
        
        signed = signer.sign_url(
            url="https://example.com/file.jpg?width=100&height=100",
            file_id=12345,
        )
        
        assert "width=100" in signed.url
        assert "height=100" in signed.url


@pytest.mark.media
class TestUrlSigningIntegration:
    """Integration tests for URL signing via media module."""

    def test_sign_uploaded_file(self, media_module, user_pool, sample_image_bytes):
        """Test signing URL for uploaded file."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="sign_test.jpg",
        )
        
        signed = media_module.sign_url(result.file_id)
        
        assert signed.url is not None
        assert signed.file_id == result.file_id

    def test_verify_signed_file_url(self, media_module, user_pool, sample_image_bytes):
        """Test verifying signed URL for uploaded file."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="verify_test.jpg",
        )
        
        signed = media_module.sign_url(result.file_id)
        is_valid, file_id = media_module.verify_signed_url(signed.url)
        
        assert is_valid is True
        assert file_id == result.file_id

    def test_sign_with_custom_expiry(self, media_module, user_pool, sample_image_bytes):
        """Test signing with custom expiration."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="expiry_test.jpg",
        )
        
        signed = media_module.sign_url(result.file_id, expires_in=60)
        
        assert signed.expires_at is not None
