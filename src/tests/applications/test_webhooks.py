"""Tests for application webhook signature verification."""

import pytest
import hmac
import hashlib

from src.core.applications.exceptions import WebhookSignatureError


@pytest.mark.applications
class TestWebhooks:
    """Tests for webhook signature verification."""

    def test_verify_valid_signature(self, app_manager, test_user):
        """Test verifying a valid webhook signature."""
        app = app_manager.create_application(owner_id=test_user.id, name="Webhook App")
        body = b'{"type": 1}'
        timestamp = "1234567890"
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = timestamp.encode() + body
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        result = app_manager.verify_webhook_signature(body, expected, timestamp)
        assert result is True

    def test_verify_invalid_signature(self, app_manager, test_user):
        """Test that invalid signature raises error."""
        app = app_manager.create_application(owner_id=test_user.id, name="Bad Sig App")
        body = b'{"type": 1}'
        timestamp = "1234567890"
        bad_signature = "invalid_signature"

        with pytest.raises(WebhookSignatureError):
            app_manager.verify_webhook_signature(body, bad_signature, timestamp)

    def test_verify_tampered_body(self, app_manager, test_user):
        """Test that tampered body causes signature mismatch."""
        app = app_manager.create_application(owner_id=test_user.id, name="Tamper App")
        original_body = b'{"type": 1}'
        timestamp = "1234567890"
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = timestamp.encode() + original_body
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        tampered_body = b'{"type": 2}'
        with pytest.raises(WebhookSignatureError):
            app_manager.verify_webhook_signature(tampered_body, expected, timestamp)

    def test_verify_wrong_timestamp(self, app_manager, test_user):
        """Test that wrong timestamp causes signature mismatch."""
        app = app_manager.create_application(owner_id=test_user.id, name="Time App")
        body = b'{"type": 1}'
        original_timestamp = "1234567890"
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = original_timestamp.encode() + body
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        wrong_timestamp = "9999999999"
        with pytest.raises(WebhookSignatureError):
            app_manager.verify_webhook_signature(body, expected, wrong_timestamp)
