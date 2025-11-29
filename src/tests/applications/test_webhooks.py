"""
Tests for webhook signature verification.
"""

import pytest
import hmac
import hashlib


@pytest.mark.applications
@pytest.mark.integration
class TestWebhookSignature:
    """Tests for webhook signature verification."""

    def test_verify_valid_signature(self, modules):
        """Test verifying a valid webhook signature."""
        body = b'{"type": 1}'
        timestamp = "1234567890"
        secret = "plexichat-webhook-secret"

        message = timestamp.encode() + body
        signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        result = modules.applications.verify_webhook_signature(
            body=body,
            signature=signature,
            timestamp=timestamp,
        )

        assert result is True

    def test_verify_invalid_signature(self, modules):
        """Test verifying an invalid webhook signature."""
        body = b'{"type": 1}'
        timestamp = "1234567890"

        with pytest.raises(modules.applications.WebhookSignatureError):
            modules.applications.verify_webhook_signature(
                body=body,
                signature="invalid_signature",
                timestamp=timestamp,
            )

    def test_verify_tampered_body(self, modules):
        """Test that tampered body fails verification."""
        original_body = b'{"type": 1}'
        tampered_body = b'{"type": 2}'
        timestamp = "1234567890"
        secret = "plexichat-webhook-secret"

        message = timestamp.encode() + original_body
        signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        with pytest.raises(modules.applications.WebhookSignatureError):
            modules.applications.verify_webhook_signature(
                body=tampered_body,
                signature=signature,
                timestamp=timestamp,
            )

    def test_verify_tampered_timestamp(self, modules):
        """Test that tampered timestamp fails verification."""
        body = b'{"type": 1}'
        original_timestamp = "1234567890"
        tampered_timestamp = "9999999999"
        secret = "plexichat-webhook-secret"

        message = original_timestamp.encode() + body
        signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        with pytest.raises(modules.applications.WebhookSignatureError):
            modules.applications.verify_webhook_signature(
                body=body,
                signature=signature,
                timestamp=tampered_timestamp,
            )
